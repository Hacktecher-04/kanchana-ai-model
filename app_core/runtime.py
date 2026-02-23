from __future__ import annotations

import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from .settings import Settings, tail_text


class TokenBucketLimiter:
    def __init__(self, rate_per_minute: int, burst: int) -> None:
        self.rate_per_second = max(1, rate_per_minute) / 60.0
        self.burst = max(1, burst)
        self.state: dict[str, tuple[float, float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        now = time.monotonic()
        with self.lock:
            tokens, last = self.state.get(key, (float(self.burst), now))
            refill = (now - last) * self.rate_per_second
            tokens = min(float(self.burst), tokens + refill)
            if tokens < 1.0:
                self.state[key] = (tokens, now)
                retry_after = (1.0 - tokens) / self.rate_per_second
                return False, max(0.1, retry_after)

            tokens -= 1.0
            self.state[key] = (tokens, now)
            return True, 0.0


class LlamaServerManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.process: Optional[subprocess.Popen[str]] = None
        self.client = httpx.AsyncClient(timeout=settings.request_timeout_seconds)
        self.stdout_log_path = Path("llama_server.out.log")
        self.stderr_log_path = Path("llama_server.err.log")
        self._stdout_log_file: Optional[Any] = None
        self._stderr_log_file: Optional[Any] = None

    def _close_log_files(self) -> None:
        for fp in (self._stdout_log_file, self._stderr_log_file):
            if fp is None:
                continue
            try:
                fp.close()
            except Exception:
                pass
        self._stdout_log_file = None
        self._stderr_log_file = None

    def _build_command(self) -> list[str]:
        if not self.settings.llama_server_path.exists():
            raise FileNotFoundError(
                f"llama-server not found: {self.settings.llama_server_path}"
            )

        cmd = [
            str(self.settings.llama_server_path),
            "--host",
            self.settings.llama_host,
            "--port",
            str(self.settings.llama_port),
            "-c",
            str(self.settings.llama_ctx),
            "-b",
            str(max(32, self.settings.llama_batch)),
            "-ub",
            str(max(16, self.settings.llama_ubatch)),
            "-fa",
            (
                self.settings.llama_flash_attn
                if self.settings.llama_flash_attn in {"on", "off", "auto"}
                else "off"
            ),
            "-t",
            str(self.settings.llama_threads),
            "-ngl",
            str(self.settings.llama_gpu_layers),
            "-np",
            str(max(1, self.settings.llama_parallel)),
            "--api-key",
            self.settings.llama_internal_api_key,
            "--reasoning-budget",
            "0",
            "--jinja",
        ]
        if self.settings.llama_no_repack:
            cmd.append("--no-repack")

        if self.settings.llama_model_path is not None:
            if not self.settings.llama_model_path.exists():
                raise FileNotFoundError(
                    f"Model not found: {self.settings.llama_model_path}"
                )
            cmd += ["-m", str(self.settings.llama_model_path)]
        else:
            cmd += ["-hf", self.settings.llama_hf_repo or ""]
            if self.settings.llama_hf_file:
                cmd += ["-hff", self.settings.llama_hf_file]
        return cmd

    async def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return

        cmd = self._build_command()
        self._close_log_files()
        self._stdout_log_file = self.stdout_log_path.open(
            "w", encoding="utf-8", errors="replace"
        )
        self._stderr_log_file = self.stderr_log_path.open(
            "w", encoding="utf-8", errors="replace"
        )
        self.process = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=self._stdout_log_file,
            stderr=self._stderr_log_file,
            text=True,
        )

        ready = False
        for _ in range(180):
            if self.process.poll() is not None:
                err_tail = tail_text(self.stderr_log_path, n_lines=80)
                out_tail = tail_text(self.stdout_log_path, n_lines=40)
                details = "\n".join(
                    part for part in [err_tail, out_tail] if part.strip()
                ).strip()
                self._close_log_files()
                if details:
                    raise RuntimeError(
                        f"llama-server exited early during startup:\n{details}"
                    )
                raise RuntimeError("llama-server exited early during startup")
            try:
                response = await self.client.get(f"{self.settings.llama_base_url}/health")
                if response.status_code == 200:
                    ready = True
                    break
            except httpx.HTTPError:
                pass
            await _async_sleep(0.5)

        if not ready:
            err_tail = tail_text(self.stderr_log_path, n_lines=80)
            out_tail = tail_text(self.stdout_log_path, n_lines=40)
            details = "\n".join(
                part for part in [err_tail, out_tail] if part.strip()
            ).strip()
            self._close_log_files()
            if details:
                raise RuntimeError(
                    f"llama-server did not become ready in time:\n{details}"
                )
            raise RuntimeError("llama-server did not become ready in time")

    async def stop(self) -> None:
        proc = self.process
        self.process = None
        if proc is None:
            self._close_log_files()
            return
        if proc.poll() is not None:
            self._close_log_files()
            return
        proc.send_signal(signal.SIGTERM)
        for _ in range(40):
            if proc.poll() is not None:
                break
            await _async_sleep(0.1)
        if proc.poll() is None:
            proc.kill()
        self._close_log_files()

    async def close(self) -> None:
        await self.client.aclose()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
        repeat_penalty: float,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.settings.llama_internal_api_key}",
            "x-api-key": self.settings.llama_internal_api_key,
        }
        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "stream": False,
        }
        response = await self.client.post(
            f"{self.settings.llama_base_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        return response.json()


async def _async_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)

