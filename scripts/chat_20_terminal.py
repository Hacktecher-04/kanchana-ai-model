#!/usr/bin/env python3
"""
Interactive terminal chat client for fixed-turn testing.

Default behavior:
- Connects to local API at http://127.0.0.1:8000
- Runs up to 20 user messages
- Preserves recent chat history for context
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from typing import Any

import httpx

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run interactive 20-turn chat test")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--turns", type=int, default=20)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--client-secret", default="")
    parser.add_argument("--max-history", type=int, default=80)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--system-prompt-file", default="")
    parser.add_argument("--user-id", default="")
    parser.add_argument("--session-id", default="terminal-session")
    parser.add_argument("--model-name", default="")
    parser.add_argument("--behavior-profile", default="")
    parser.add_argument("--prebuilt-prompt-file", default="")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--top-p", type=float, default=None)
    parser.add_argument("--repeat-penalty", type=float, default=None)
    parser.add_argument(
        "--transcript-dir",
        default="transcripts",
        help="Directory for saving plain-text transcript",
    )
    return parser.parse_args()


def load_api_key(cli_key: str) -> str:
    key = (cli_key or "").strip()
    if key:
        return key

    key = os.getenv("APP_API_KEY", "").strip()
    if key:
        return key

    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("APP_API_KEY="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value

    raise RuntimeError("APP_API_KEY not found. Pass --api-key or set it in .env")


def load_client_secret(cli_secret: str) -> str:
    secret = (cli_secret or "").strip()
    if secret:
        return secret

    secret = os.getenv("APP_CLIENT_SECRET", "").strip()
    if secret:
        return secret

    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("APP_CLIENT_SECRET="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value

    raise RuntimeError(
        "APP_CLIENT_SECRET not found. Pass --client-secret or set it in .env"
    )


def read_system_prompt(path_raw: str) -> str | None:
    path_raw = (path_raw or "").strip()
    if not path_raw:
        return None
    path = Path(path_raw)
    if not path.exists():
        raise FileNotFoundError(f"System prompt file not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def read_optional_text(path_raw: str) -> str | None:
    path_raw = (path_raw or "").strip()
    if not path_raw:
        return None
    path = Path(path_raw)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def ensure_health(client: httpx.Client, base_url: str) -> None:
    response = client.get(f"{base_url}/health")
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "ok":
        raise RuntimeError("API health check returned unexpected payload")


def ensure_chat_ready(
    client: httpx.Client, base_url: str, api_key: str, client_secret: str
) -> None:
    headers = {
        "x-api-key": api_key,
        "x-client-secret": client_secret,
        "content-type": "application/json",
    }
    payload = {"message": "ping", "history": [], "max_tokens": 24}
    response = client.post(f"{base_url}/v1/chat", headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    reply = str(data.get("reply", "")).strip()
    if not reply:
        raise RuntimeError("Chat endpoint returned empty reply")


def save_transcript(out_dir: Path, rows: list[tuple[str, str]]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"chat_20_{ts}.txt"
    lines: list[str] = []
    for speaker, text in rows:
        lines.append(f"{speaker}: {text}")
    out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_file


def post_chat(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    client_secret: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    headers = {
        "x-api-key": api_key,
        "x-client-secret": client_secret,
        "content-type": "application/json",
    }
    response = client.post(f"{base_url}/v1/chat", headers=headers, json=payload)
    response.raise_for_status()
    return response.json()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if load_dotenv is not None:
        load_dotenv(".env", override=False)

    args = parse_args()
    if args.turns <= 0:
        print("--turns must be greater than 0", file=sys.stderr)
        return 2
    if args.max_history < 0:
        print("--max-history must be >= 0", file=sys.stderr)
        return 2

    api_key = load_api_key(args.api_key)
    client_secret = load_client_secret(args.client_secret)
    system_prompt = read_system_prompt(args.system_prompt_file)
    prebuilt_prompt = read_optional_text(args.prebuilt_prompt_file)

    base_url = args.api_url.rstrip("/")
    history: list[dict[str, str]] = []
    transcript: list[tuple[str, str]] = []

    with httpx.Client(timeout=args.timeout) as client:
        try:
            ensure_health(client, base_url)
            ensure_chat_ready(client, base_url, api_key, client_secret)
        except Exception as exc:
            print(f"API preflight failed: {exc}", file=sys.stderr)
            return 2

        print(f"Chat test ready: {args.turns} turns")
        print("Commands: /exit to quit early\n")

        sent = 0
        while sent < args.turns:
            turn = sent + 1
            try:
                user_msg = input(f"you[{turn}/{args.turns}]> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nInterrupted.")
                break

            if not user_msg:
                continue
            if user_msg.lower() in {"/exit", "/quit"}:
                break

            payload: dict[str, Any] = {
                "message": user_msg,
                "history": history,
            }
            context: dict[str, Any] = {}
            if args.user_id.strip():
                context["user_id"] = args.user_id.strip()
            if args.session_id.strip():
                context["session_id"] = args.session_id.strip()
            if args.model_name.strip():
                context["model_name"] = args.model_name.strip()
            if args.behavior_profile.strip():
                context["behavior_profile"] = args.behavior_profile.strip()
            if prebuilt_prompt:
                context["prebuilt_prompt"] = prebuilt_prompt
            if context:
                payload["context"] = context
            if system_prompt is not None:
                payload["system_prompt"] = system_prompt
            if args.temperature is not None:
                payload["temperature"] = args.temperature
            if args.top_p is not None:
                payload["top_p"] = args.top_p
            if args.repeat_penalty is not None:
                payload["repeat_penalty"] = args.repeat_penalty

            try:
                data = post_chat(client, base_url, api_key, client_secret, payload)
            except httpx.HTTPStatusError as exc:
                body = exc.response.text.strip()
                print(
                    f"Request failed ({exc.response.status_code}): {body}",
                    file=sys.stderr,
                )
                continue
            except httpx.ConnectError as exc:
                print(f"Request failed: {exc}", file=sys.stderr)
                print(
                    "API connection closed/refused during chat. Restart API and run test again.",
                    file=sys.stderr,
                )
                break
            except httpx.HTTPError as exc:
                print(f"Request failed: {exc}", file=sys.stderr)
                continue

            reply = str(data.get("reply", "")).strip() or "..."
            print(f"ai[{turn}/{args.turns}]> {reply}\n")

            transcript.append(("you", user_msg))
            transcript.append(("ai", reply))

            history.append({"role": "user", "content": user_msg})
            history.append({"role": "assistant", "content": reply})
            if args.max_history > 0 and len(history) > args.max_history:
                history = history[-args.max_history :]
            if args.max_history == 0:
                history = []

            sent += 1

    if transcript:
        out_file = save_transcript(Path(args.transcript_dir), transcript)
        print(f"Transcript saved: {out_file}")
    else:
        print("No transcript saved (no turns completed).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
