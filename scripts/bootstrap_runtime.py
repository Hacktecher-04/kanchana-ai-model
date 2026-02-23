#!/usr/bin/env python3
"""
Prepare llama.cpp runtime and optional model file.

Usage:
  python scripts/bootstrap_runtime.py
"""

from __future__ import annotations

import os
import platform
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path


LLAMA_RELEASE = os.getenv("LLAMA_CPP_RELEASE", "b8123")


def download(url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading: {url}")
    with urllib.request.urlopen(url) as src, output_path.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    print(f"Saved: {output_path}")


def _normalize_runtime_layout(runtime_dir: Path, binary_name: str) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    expected = runtime_dir / binary_name
    if expected.exists():
        return expected

    candidates = sorted(
        (p for p in runtime_dir.rglob(binary_name) if p.is_file()),
        key=lambda p: len(p.parts),
    )
    if not candidates:
        raise FileNotFoundError(
            f"{binary_name} not found after extraction under: {runtime_dir}"
        )

    source_parent = candidates[0].parent
    if source_parent != runtime_dir:
        for item in source_parent.iterdir():
            target = runtime_dir / item.name
            if target.exists():
                continue
            shutil.move(str(item), str(target))

    if not expected.exists():
        raise FileNotFoundError(f"{binary_name} not found at expected path: {expected}")
    return expected


def ensure_llama_server() -> None:
    system = platform.system().lower()
    arch = platform.machine().lower()

    if system == "windows":
        runtime_dir = Path("bin/llama-cpp")
        server_path = runtime_dir / "llama-server.exe"
        asset = f"llama-{LLAMA_RELEASE}-bin-win-cpu-x64.zip"
        release_file = Path(f"bin/{asset}")
        url = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_RELEASE}/{asset}"
        if server_path.exists():
            print(f"llama-server already exists: {server_path}")
            return
        download(url, release_file)
        with zipfile.ZipFile(release_file, "r") as zf:
            zf.extractall(runtime_dir)
        _normalize_runtime_layout(runtime_dir, "llama-server.exe")
        print(f"Extracted runtime to: {runtime_dir}")
        return

    if system == "linux" and arch in {"x86_64", "amd64"}:
        runtime_dir = Path("bin/llama-cpp")
        server_path = runtime_dir / "llama-server"
        asset = f"llama-{LLAMA_RELEASE}-bin-ubuntu-x64.tar.gz"
        release_file = Path(f"bin/{asset}")
        url = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_RELEASE}/{asset}"
        if server_path.exists():
            print(f"llama-server already exists: {server_path}")
            return
        download(url, release_file)
        with tarfile.open(release_file, "r:gz") as tf:
            tf.extractall(runtime_dir)
        server_path = _normalize_runtime_layout(runtime_dir, "llama-server")
        server_path.chmod(0o755)
        print(f"Extracted runtime to: {runtime_dir}")
        return

    raise RuntimeError(f"Unsupported platform: system={system} arch={arch}")


def ensure_model() -> None:
    hf_repo = os.getenv("LLAMA_HF_REPO", "").strip()
    model_path_raw = os.getenv("LLAMA_MODEL_PATH", "").strip()
    if not model_path_raw and hf_repo:
        print(f"Skipping local model download; using HF repo: {hf_repo}")
        return

    model_path = Path(model_path_raw or "models/qwen2.5-0.5b-instruct-q4_k_m.gguf")
    model_url = os.getenv(
        "LLAMA_MODEL_URL",
        "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf?download=true",
    ).strip()

    if model_path.exists():
        print(f"Model already exists: {model_path}")
        return

    download(model_url, model_path)


def main() -> int:
    ensure_llama_server()
    ensure_model()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
