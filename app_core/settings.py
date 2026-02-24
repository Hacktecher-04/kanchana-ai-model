from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"File is empty: {path}")
    return text


def tail_text(path: Path, n_lines: int = 80) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    if not lines:
        return ""
    return "\n".join(lines[-n_lines:])


@dataclass
class Settings:
    app_api_key: str
    app_client_secret: str
    llama_host: str
    llama_port: int
    llama_server_path: Path
    llama_model_path: Optional[Path]
    llama_hf_repo: Optional[str]
    llama_hf_file: Optional[str]
    llama_ctx: int
    llama_batch: int
    llama_ubatch: int
    llama_flash_attn: str
    llama_threads: int
    llama_gpu_layers: int
    llama_parallel: int
    llama_no_repack: bool
    llama_internal_api_key: str
    system_prompt_file: Path
    max_input_chars: int
    max_output_tokens: int
    default_temperature: float
    default_top_p: float
    default_repeat_penalty: float
    response_checkpoints: int
    history_user_turns: int
    memory_summary_items: int
    request_timeout_seconds: int
    fast_response_deadline_seconds: float
    runtime_start_wait_seconds: float
    rate_limit_rpm: int
    rate_limit_burst: int
    enable_async_learning_on_chat: bool
    async_learning_max_concurrency: int
    ultra_fast_mode: bool
    deliberate_human_mode: bool
    enable_web_fallback: bool
    web_lookup_for_facts: bool
    web_lookup_timeout_seconds: int
    web_cache_max_items: int
    enable_relationship_learning: bool
    relationship_bridge_mode: bool
    relationship_learning_on_chat: bool
    relationship_kb_path: Path
    relationship_kb_max_items: int
    relationship_learning_timeout_seconds: int
    enable_limits_guardrails: bool
    limits_learning_on_chat: bool
    limits_kb_path: Path
    limits_kb_max_items: int
    limits_learning_timeout_seconds: int
    enable_background_learning: bool
    background_learning_sleep_minutes: int
    background_relationship_count: int
    background_limits_count: int
    enable_long_term_memory: bool
    memory_store_path: Path
    memory_store_max_users: int
    memory_store_max_items_per_user: int
    memory_store_read_items: int
    memory_store_write_items: int

    @property
    def llama_base_url(self) -> str:
        return f"http://{self.llama_host}:{self.llama_port}"


def load_settings() -> Settings:
    cpu_threads = max(1, os.cpu_count() or 1)
    default_server_path = (
        "bin/llama-cpp/llama-server.exe"
        if os.name == "nt"
        else "bin/llama-cpp/llama-server"
    )
    app_api_key = os.getenv("APP_API_KEY", "").strip()
    if not app_api_key:
        raise RuntimeError(
            "APP_API_KEY is required. Set a strong value before starting API."
        )
    app_client_secret = os.getenv("APP_CLIENT_SECRET", "").strip()
    if not app_client_secret:
        raise RuntimeError(
            "APP_CLIENT_SECRET is required. Set a strong value before starting API."
        )

    llama_model_path_raw = os.getenv("LLAMA_MODEL_PATH", "").strip()
    llama_model_path = Path(llama_model_path_raw) if llama_model_path_raw else None
    llama_hf_repo = os.getenv("LLAMA_HF_REPO", "").strip() or None
    llama_hf_file = os.getenv("LLAMA_HF_FILE", "").strip() or None

    if llama_model_path is None and llama_hf_repo is None:
        default_model = Path("models/tinyllama-1.1b-chat-v0.3.Q2_K.gguf")
        if default_model.exists():
            llama_model_path = default_model
        else:
            raise RuntimeError(
                "No model configured. Set LLAMA_MODEL_PATH or LLAMA_HF_REPO."
            )

    return Settings(
        app_api_key=app_api_key,
        app_client_secret=app_client_secret,
        llama_host=os.getenv("LLAMA_HOST", "127.0.0.1"),
        llama_port=int(os.getenv("LLAMA_PORT", "8081")),
        llama_server_path=Path(
            os.getenv("LLAMA_SERVER_PATH", default_server_path)
        ),
        llama_model_path=llama_model_path,
        llama_hf_repo=llama_hf_repo,
        llama_hf_file=llama_hf_file,
        llama_ctx=int(os.getenv("LLAMA_CTX", "4096")),
        llama_batch=int(os.getenv("LLAMA_BATCH", "256")),
        llama_ubatch=int(os.getenv("LLAMA_UBATCH", "128")),
        llama_flash_attn=os.getenv("LLAMA_FLASH_ATTN", "off").strip().lower(),
        llama_threads=int(os.getenv("LLAMA_THREADS", str(cpu_threads))),
        llama_gpu_layers=int(os.getenv("LLAMA_GPU_LAYERS", "0")),
        llama_parallel=int(os.getenv("LLAMA_PARALLEL", "1")),
        llama_no_repack=(
            os.getenv("LLAMA_NO_REPACK", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        llama_internal_api_key=os.getenv(
            "LLAMA_INTERNAL_API_KEY", secrets.token_urlsafe(24)
        ),
        system_prompt_file=Path(
            os.getenv("SYSTEM_PROMPT_FILE", "prompts/system_prompt.txt")
        ),
        max_input_chars=int(os.getenv("MAX_INPUT_CHARS", "2000")),
        max_output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "72")),
        default_temperature=float(os.getenv("DEFAULT_TEMPERATURE", "0.52")),
        default_top_p=float(os.getenv("DEFAULT_TOP_P", "0.82")),
        default_repeat_penalty=float(os.getenv("DEFAULT_REPEAT_PENALTY", "1.30")),
        response_checkpoints=int(os.getenv("RESPONSE_CHECKPOINTS", "2")),
        history_user_turns=int(os.getenv("HISTORY_USER_TURNS", "40")),
        memory_summary_items=int(os.getenv("MEMORY_SUMMARY_ITEMS", "10")),
        request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "300")),
        fast_response_deadline_seconds=float(
            os.getenv("FAST_RESPONSE_DEADLINE_SECONDS", "90.0")
        ),
        runtime_start_wait_seconds=float(
            os.getenv("RUNTIME_START_WAIT_SECONDS", "240.0")
        ),
        rate_limit_rpm=int(os.getenv("RATE_LIMIT_RPM", "60")),
        rate_limit_burst=int(os.getenv("RATE_LIMIT_BURST", "20")),
        enable_async_learning_on_chat=(
            os.getenv("ENABLE_ASYNC_LEARNING_ON_CHAT", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        async_learning_max_concurrency=int(
            os.getenv("ASYNC_LEARNING_MAX_CONCURRENCY", "1")
        ),
        ultra_fast_mode=(
            os.getenv("ULTRA_FAST_MODE", "0").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        deliberate_human_mode=(
            os.getenv("DELIBERATE_HUMAN_MODE", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        enable_web_fallback=(
            os.getenv("ENABLE_WEB_FALLBACK", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        web_lookup_for_facts=(
            os.getenv("WEB_LOOKUP_FOR_FACTS", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        web_lookup_timeout_seconds=int(os.getenv("WEB_LOOKUP_TIMEOUT_SECONDS", "12")),
        web_cache_max_items=int(os.getenv("WEB_CACHE_MAX_ITEMS", "800")),
        enable_relationship_learning=(
            os.getenv("ENABLE_RELATIONSHIP_LEARNING", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        relationship_bridge_mode=(
            os.getenv("RELATIONSHIP_BRIDGE_MODE", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        relationship_learning_on_chat=(
            os.getenv("RELATIONSHIP_LEARNING_ON_CHAT", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        relationship_kb_path=Path(
            os.getenv("RELATIONSHIP_KB_PATH", "knowledge_relationship.json")
        ),
        relationship_kb_max_items=int(os.getenv("RELATIONSHIP_KB_MAX_ITEMS", "5000")),
        relationship_learning_timeout_seconds=int(
            os.getenv("RELATIONSHIP_LEARNING_TIMEOUT_SECONDS", "14")
        ),
        enable_limits_guardrails=(
            os.getenv("ENABLE_LIMITS_GUARDRAILS", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        limits_learning_on_chat=(
            os.getenv("LIMITS_LEARNING_ON_CHAT", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        limits_kb_path=Path(os.getenv("LIMITS_KB_PATH", "knowledge_limits.json")),
        limits_kb_max_items=int(os.getenv("LIMITS_KB_MAX_ITEMS", "2500")),
        limits_learning_timeout_seconds=int(
            os.getenv("LIMITS_LEARNING_TIMEOUT_SECONDS", "14")
        ),
        enable_background_learning=(
            os.getenv("ENABLE_BACKGROUND_LEARNING", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        background_learning_sleep_minutes=int(
            os.getenv("BACKGROUND_LEARNING_SLEEP_MINUTES", "180")
        ),
        background_relationship_count=int(
            os.getenv("BACKGROUND_RELATIONSHIP_COUNT", "24")
        ),
        background_limits_count=int(os.getenv("BACKGROUND_LIMITS_COUNT", "12")),
        enable_long_term_memory=(
            os.getenv("ENABLE_LONG_TERM_MEMORY", "1").strip().lower()
            not in {"0", "false", "no", "off"}
        ),
        memory_store_path=Path(os.getenv("MEMORY_STORE_PATH", "memory_store.json")),
        memory_store_max_users=int(os.getenv("MEMORY_STORE_MAX_USERS", "2000")),
        memory_store_max_items_per_user=int(
            os.getenv("MEMORY_STORE_MAX_ITEMS_PER_USER", "160")
        ),
        memory_store_read_items=int(os.getenv("MEMORY_STORE_READ_ITEMS", "10")),
        memory_store_write_items=int(os.getenv("MEMORY_STORE_WRITE_ITEMS", "4")),
    )
