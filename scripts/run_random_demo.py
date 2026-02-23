#!/usr/bin/env python3
"""
Run automated random-question demo against /v1/chat endpoint.
Saves plain-text transcript and JSON summary in transcripts/.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

import httpx

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


QUESTION_POOL = [
    "hello bhai kaise ho",
    "tumhari strongest skill kya hai",
    "penicillin kisne discover kiya",
    "distance between earth and moon kitna hota hai",
    "aaj ka ek short motivational line do",
    "kaun sa planet sabse bada hai",
    "who is the CEO of NVIDIA today?",
    "india ka national animal kya hai",
    "agar mujhe confidence build karna ho to first step kya",
    "ek line me photosynthesis samjhao",
    "kaise pata chale answer correct hai ya guess",
    "how to learn JavaScript in 30 days roadmap",
    "bhai ek light joke suna",
    "france ki capital kya hai",
    "agar neend nahi aaye to kya karu",
    "aaj weather check karne ka best tareeka kya hai",
    "bitcoin kya hota hai simple words me",
    "bhai interview me first impression kaise achha kare",
    "who invented the mobile phone",
    "agar mai upset hoon to quick calm kaise karu",
    "roman hindi me short reply do, AI kya hota hai",
    "time management ke 3 practical tips do",
    "what is quantum entanglement in one line",
    "ek startup idea do low budget me",
    "ghar pe English speaking improve kaise karu",
    "networking skill kaise improve karte hain",
    "tell me one book for better thinking",
    "kya tum coding debug kar sakte ho",
    "CSS flexbox aur grid me farq kya hai",
    "bhai mujhe discipline build karna hai, plan do",
    "how to avoid procrastination quickly",
    "kya tum internet se latest info la sakte ho",
    "agar koi rude ho to respond kaise kare",
    "productivity ke liye pomodoro useful hai kya",
    "ek choti si sales pitch line banao",
    "top 3 habits for students batao",
    "resume me sabse common galti kya hoti hai",
    "agar mujhe app banana ho to kaha se start karu",
    "who discovered gravity",
    "machine learning aur deep learning me difference",
    "bhai communication improve ka short trick",
    "healthy morning routine ka sample do",
    "how to become better at problem solving",
    "best way to remember what I study",
    "internet se batao latest gold price trend",
    "agar answer nahi pata ho to tum kya karte ho",
    "ek subtle playful line do",
    "SQL kya hota hai beginner level me batao",
    "short me batao API secure kaise karte hain",
    "agar user random sawal puche to best response strategy kya",
    "kaise decide kare ki kaun sa career choose karna chahiye",
    "mujhe negotiation sikhao one tip",
    "how to write better prompts",
    "ek mini daily plan bana do 6 hour study ka",
    "agar fail ho jaye to comeback kaise kare",
    "bhai mujhe smart tareeke se padhna hai",
    "what is cloud computing simple explanation",
    "kya tum meri hindi aur english mix samajh sakte ho",
    "ek line me explain karo critical thinking",
    "AI response repetitive kyu hota hai",
    "agar koi bole tum boring ho to kya bolu",
    "give one practical confidence exercise",
    "DNS kya hota hai",
    "internet source ke sath batao mars par life possible hai kya",
    "bhai mujhe ek realistic weekend plan do",
    "how to ask better questions to AI",
    "git branch ka use kya hota hai",
    "python list aur tuple me difference",
    "ek calm reply do jab stress high ho",
    "who is founder of Microsoft",
    "internet se batao world population approx kitni hai",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run random QA demo")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--client-secret", default="")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--max-history", type=int, default=80)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", default="transcripts")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Run direct chat pipeline (no external HTTP API process needed).",
    )
    return parser.parse_args()


def load_api_key(cli_key: str) -> str:
    key = (cli_key or "").strip()
    if key:
        return key

    env_key = os.getenv("APP_API_KEY", "").strip()
    if env_key:
        return env_key

    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("APP_API_KEY="):
                raw = line.split("=", 1)[1].strip()
                if raw:
                    return raw
    raise RuntimeError("APP_API_KEY not found. Pass --api-key or set it in .env")


def load_client_secret(cli_secret: str) -> str:
    secret = (cli_secret or "").strip()
    if secret:
        return secret

    env_secret = os.getenv("APP_CLIENT_SECRET", "").strip()
    if env_secret:
        return env_secret

    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("APP_CLIENT_SECRET="):
                raw = line.split("=", 1)[1].strip()
                if raw:
                    return raw
    raise RuntimeError(
        "APP_CLIENT_SECRET not found. Pass --client-secret or set it in .env"
    )


def make_questions(count: int, seed: int) -> list[str]:
    random.seed(seed)
    pool = QUESTION_POOL[:]
    if count <= len(pool):
        random.shuffle(pool)
        return pool[:count]

    out: list[str] = []
    while len(out) < count:
        random.shuffle(pool)
        out.extend(pool)
    return out[:count]


def save_outputs(
    rows: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
    mode: str,
    web_hits: int,
    errors: int,
) -> tuple[Path, Path]:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_file = out_dir / f"random_demo_{args.count}_{ts}.txt"
    json_file = out_dir / f"random_demo_{args.count}_{ts}.json"

    txt_lines: list[str] = []
    for row in rows:
        txt_lines.append(f"Q{row['index']}: {row['question']}")
        txt_lines.append(f"A{row['index']}: {row['reply']}")
        txt_lines.append(f"M{row['index']}: {row['model']}")
        txt_lines.append("")
    txt_file.write_text("\n".join(txt_lines).rstrip() + "\n", encoding="utf-8")

    summary = {
        "count": args.count,
        "seed": args.seed,
        "mode": mode,
        "api_url": args.api_url.rstrip("/"),
        "web_fallback_hits": web_hits,
        "errors": errors,
        "transcript_file": str(txt_file).replace("\\", "/"),
        "rows": rows,
    }
    json_file.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return txt_file, json_file


def run_http_mode(args: argparse.Namespace, questions: list[str]) -> tuple[list[dict[str, Any]], int, int]:
    api_key = load_api_key(args.api_key)
    client_secret = load_client_secret(args.client_secret)
    base_url = args.api_url.rstrip("/")
    headers = {
        "x-api-key": api_key,
        "x-client-secret": client_secret,
        "content-type": "application/json",
    }
    history: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    web_hits = 0
    errors = 0

    with httpx.Client(timeout=args.timeout) as client:
        health = client.get(f"{base_url}/health")
        health.raise_for_status()

        for idx, q in enumerate(questions, start=1):
            payload: dict[str, Any] = {"message": q, "history": history}
            try:
                resp = client.post(f"{base_url}/v1/chat", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                reply = str(data.get("reply", "")).strip()
                model = str(data.get("model", "")).strip()
                if model == "web-fallback":
                    web_hits += 1
            except Exception as exc:
                errors += 1
                reply = f"[ERROR] {exc}"
                model = "request-error"

            rows.append(
                {
                    "index": idx,
                    "question": q,
                    "reply": reply,
                    "model": model,
                }
            )
            history.append({"role": "user", "content": q})
            history.append({"role": "assistant", "content": reply})
            if args.max_history == 0:
                history = []
            elif len(history) > args.max_history:
                history = history[-args.max_history :]

    return rows, web_hits, errors


async def run_direct_mode(
    args: argparse.Namespace, questions: list[str]
) -> tuple[list[dict[str, Any]], int, int]:
    # Late imports so HTTP mode does not require app runtime modules.
    from app_core.api import chat, manager
    from app_core.schemas import ChatRequest

    history: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    web_hits = 0
    errors = 0

    await manager.start()
    try:
        for idx, q in enumerate(questions, start=1):
            try:
                payload = ChatRequest(message=q, history=history)
                result = await chat(payload, _="direct-local")
                reply = (result.reply or "").strip()
                model = str(result.model or "").strip()
                if model == "web-fallback":
                    web_hits += 1
            except Exception as exc:
                errors += 1
                reply = f"[ERROR] {exc}"
                model = "request-error"

            rows.append(
                {
                    "index": idx,
                    "question": q,
                    "reply": reply,
                    "model": model,
                }
            )
            history.append({"role": "user", "content": q})
            history.append({"role": "assistant", "content": reply})
            if args.max_history == 0:
                history = []
            elif len(history) > args.max_history:
                history = history[-args.max_history :]
    finally:
        await manager.stop()
        await manager.close()

    return rows, web_hits, errors


def main() -> int:
    if load_dotenv is not None:
        load_dotenv(".env", override=False)

    args = parse_args()
    questions = make_questions(args.count, args.seed)

    if args.direct:
        rows, web_hits, errors = asyncio.run(run_direct_mode(args, questions))
        mode = "direct"
    else:
        rows, web_hits, errors = run_http_mode(args, questions)
        mode = "http"

    txt_file, json_file = save_outputs(
        rows,
        args=args,
        mode=mode,
        web_hits=web_hits,
        errors=errors,
    )

    print(f"Demo complete: {args.count} questions")
    print(f"Mode: {mode}")
    print(f"Web fallback hits: {web_hits}")
    print(f"Errors: {errors}")
    print(f"Transcript: {txt_file}")
    print(f"JSON: {json_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
