#!/usr/bin/env python3
"""
Run basic quality checks against /v1/chat-text endpoint.
Prints a plain-text report.
"""

from __future__ import annotations

import argparse
import difflib
import os
import sys
import statistics
from collections import Counter
from pathlib import Path

import httpx


PROMPTS = [
    "Give one short playful line.",
    "Now give a totally different playful line with softer tone.",
    "Respond to: You seem interesting.",
    "Give a confident reply in one sentence.",
    "Answer indirectly with subtle mystery.",
    "Keep it calm and charming in max 2 sentences.",
    "Tease a little but stay respectful.",
    "Write a surprising but short response.",
    "Reply without generic compliments.",
    "Make a fresh line with emotional weight.",
]


def normalize(text: str) -> str:
    return " ".join(text.lower().strip().split())


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def _load_env_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                raw = line.split("=", 1)[1].strip()
                if raw:
                    return raw
    return ""


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--client-secret", default="")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    api_key = (args.api_key or "").strip() or _load_env_value("APP_API_KEY")
    client_secret = (args.client_secret or "").strip() or _load_env_value(
        "APP_CLIENT_SECRET"
    )
    if not api_key:
        raise RuntimeError("APP_API_KEY not found. Pass --api-key or set it in .env")
    if not client_secret:
        raise RuntimeError(
            "APP_CLIENT_SECRET not found. Pass --client-secret or set it in .env"
        )

    headers = {
        "x-api-key": api_key,
        "x-client-secret": client_secret,
        "content-type": "application/json",
    }
    replies: list[str] = []

    with httpx.Client(timeout=args.timeout) as client:
        for index, prompt in enumerate(PROMPTS, start=1):
            payload = {"message": prompt}
            response = client.post(f"{args.api_url}/v1/chat-text", headers=headers, json=payload)
            response.raise_for_status()
            text = response.text.strip()
            replies.append(text)
            print(f"{index:02d}. {prompt}")
            print(f"    -> {text}")

    normalized = [normalize(r) for r in replies]
    duplicate_count = sum(c - 1 for c in Counter(normalized).values() if c > 1)
    lengths = [len(r.split()) for r in replies if r.strip()]
    avg_len = statistics.mean(lengths) if lengths else 0.0
    max_sim = 0.0
    max_pair = ("", "")
    for i in range(len(replies)):
        for j in range(i + 1, len(replies)):
            sim = similarity(replies[i], replies[j])
            if sim > max_sim:
                max_sim = sim
                max_pair = (replies[i], replies[j])

    print("\n=== CHECK REPORT ===")
    print(f"Total prompts: {len(PROMPTS)}")
    print(f"Exact duplicate replies: {duplicate_count}")
    print(f"Average reply length (words): {avg_len:.2f}")
    print(f"Highest pair similarity: {max_sim:.3f}")
    print("Most similar pair:")
    print(f"  A: {max_pair[0]}")
    print(f"  B: {max_pair[1]}")

    if duplicate_count == 0 and max_sim < 0.90:
        print("Result: PASS (no exact duplication; acceptable diversity)")
    else:
        print("Result: REVIEW NEEDED (repetition/diversity issue detected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
