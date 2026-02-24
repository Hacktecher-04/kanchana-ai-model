#!/usr/bin/env python3
"""
Stress-test chat behavior with up to 10,000 dummy prompts.

Goal:
- keep trying until a rolling 10-reply window looks human-like
- avoid known template/assistant-style response patterns
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import difflib
import json
import os
import random
import re
import sys
from collections import Counter, deque
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


EXPECTED_STYLE_RE = re.compile(
    (
        r"\b(short answer|factual question|verified detail|fast answer mode|"
        r"from a relationship lens|i can explain|let me explain|i can provide details|"
        r"share one specific point|give me the exact question|tell me the target outcome|"
        r"specific question|seedha sawal)\b"
    ),
    re.IGNORECASE,
)

GENERIC_TEMPLATE_RE = re.compile(
    (
        r"\b(share one specific point and i will|give me the exact question and i will|"
        r"tell me the target outcome and i will|seedha bol na, main sun raha hoon|"
        r"thoda clear bol, pakad lunga|point direct rakh)\b"
    ),
    re.IGNORECASE,
)

LOW_SIGNAL_RE = re.compile(r"^\W*(ok|okay|hmm+|huh+|alright|sure)\W*$", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="10k human-behavior loop")
    parser.add_argument("--max-questions", type=int, default=10000)
    parser.add_argument("--window", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-history", type=int, default=24)
    parser.add_argument("--out-dir", default="transcripts")
    parser.add_argument(
        "--speed-mode",
        action="store_true",
        help=(
            "Force fast runtime for large stress tests "
            "(DELIBERATE_HUMAN_MODE=0, ULTRA_FAST_MODE=1)."
        ),
    )
    parser.add_argument(
        "--enable-web-fallback",
        action="store_true",
        help="Allow web fallback during stress test (disabled by default for stable style checks).",
    )
    parser.add_argument(
        "--enable-async-learning",
        action="store_true",
        help="Allow async learning tasks during stress test (disabled by default).",
    )
    return parser.parse_args()


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _build_seed_bank() -> list[str]:
    return [
        "hello",
        "hey",
        "hi",
        "kaise ho",
        "kya haal",
        "how was your day",
        "how was your dat",
        "pyaar kya hota hai",
        "relationship ka matlab kya hota hai",
        "aaj mood off hai",
        "i feel low",
        "you miss me?",
        "k",
        "do you believe in destiny?",
        "tum jealous ho?",
        "hey after 3 days",
        "tum thode mysterious lagte ho",
        "mujhe samajh nahi aa raha kya karu",
        "seedha bolo",
        "kya chal raha hai",
        "what do you think about trust",
        "kya tum sun rahe ho",
        "aaj din kaisa tha",
        "you there?",
        "kaise reply du usko",
        "main confuse hoon",
        "ek line me bolo",
        "romantic line do",
        "thoda deep baat karte hain",
        "acha ek baat bata",
        "samjho na yaar",
        "mujhe real answer chahiye",
        "tum itne cold kyu ho",
        "ab mood better hai",
        "mujhe tease mat karo",
        "thoda tease karo",
        "kya main overthink kar raha hoon",
        "honest opinion do",
        "tumhari vibe alag hai",
        "simple raho",
        "no lecture",
        "human jaisa reply do",
        "document jaisa mat bolo",
        "chat mode me aao",
    ]


def make_dummy_questions(max_questions: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    seeds = _build_seed_bank()
    prefixes = ["", "bhai ", "yaar ", "sun ", "seedha ", "honestly ", "bata ", "plz "]
    suffixes = [
        "",
        " na",
        " please",
        " short me",
        " honestly",
        " real me",
        " bina bakwas",
        " abhi",
        " clearly",
        " calmly",
        " direct",
        " thoda feel se",
    ]
    endings = ["", "?", "??", ".", "!"]

    pool: list[str] = []
    seen: set[str] = set()
    for base in seeds:
        for p in prefixes:
            for s in suffixes:
                for e in endings:
                    q = f"{p}{base}{s}{e}".strip()
                    n = _norm_text(q)
                    if not n or n in seen:
                        continue
                    seen.add(n)
                    pool.append(q)

    # Ensure enough size by light random perturbation.
    while len(pool) < max_questions * 2:
        base = rng.choice(seeds)
        frag = rng.choice(
            [
                "ab",
                "sach me",
                "without formal tone",
                "casual me",
                "human feel ke saath",
                "thoda playful",
                "deeply",
                "short but real",
            ]
        )
        q = f"{rng.choice(prefixes)}{base} {frag}{rng.choice(endings)}".strip()
        n = _norm_text(q)
        if n in seen:
            continue
        seen.add(n)
        pool.append(q)

    rng.shuffle(pool)
    return pool[:max_questions]


def _reply_quality(reply: str) -> tuple[bool, str]:
    text = (reply or "").strip()
    low = text.lower()
    if not text or text == "...":
        return False, "empty"
    if LOW_SIGNAL_RE.fullmatch(low):
        return False, "low-signal"
    if EXPECTED_STYLE_RE.search(low):
        return False, "banned-style"
    if GENERIC_TEMPLATE_RE.search(low):
        return False, "generic-template"
    if len(text.split()) < 3:
        return False, "too-short"
    if len(text.split()) > 80:
        return False, "too-long"
    return True, "ok"


def _window_is_human(window: list[dict[str, Any]]) -> bool:
    if not window or not all(item["ok"] for item in window):
        return False
    replies = [item["reply"] for item in window]
    norms = [_norm_text(x) for x in replies]
    if len(set(norms)) < len(norms):
        return False
    for i in range(len(norms)):
        for j in range(i + 1, len(norms)):
            sim = difflib.SequenceMatcher(None, norms[i], norms[j]).ratio()
            if sim > 0.90:
                return False
    return True


async def run_loop(args: argparse.Namespace) -> dict[str, Any]:
    if args.seed == 0:
        args.seed = int(dt.datetime.now().strftime("%y%m%d%H%M%S"))
    questions = make_dummy_questions(args.max_questions, args.seed)

    if args.speed_mode:
        os.environ["DELIBERATE_HUMAN_MODE"] = "0"
        os.environ["ULTRA_FAST_MODE"] = "1"
    if not args.enable_web_fallback:
        os.environ["ENABLE_WEB_FALLBACK"] = "0"
    if not args.enable_async_learning:
        os.environ["ENABLE_ASYNC_LEARNING_ON_CHAT"] = "0"

    from app_core.api import chat, manager
    from app_core.schemas import ChatRequest, ClientContext, HistoryMessage

    behavior_profile = (
        "natural texting tone, no short-answer framing, no factual framing, "
        "no explain-process framing, emotionally aware, conversational."
    )
    context = ClientContext(
        user_id="stress-human-10k",
        session_id=f"stress-{args.seed}",
        behavior_profile=behavior_profile,
    )

    rows: list[dict[str, Any]] = []
    history: list[HistoryMessage] = []
    last_window: deque[dict[str, Any]] = deque(maxlen=max(3, args.window))
    reason_counter: Counter[str] = Counter()

    success_at = 0
    await manager.start()
    try:
        for idx, q in enumerate(questions, start=1):
            payload = ChatRequest(message=q, history=history, context=context)
            try:
                res = await chat(payload, _="stress-10k-human")
                reply = (res.reply or "").strip()
                model = str(res.model or "")
            except Exception as exc:
                reply = f"[ERROR] {exc}"
                model = "request-error"

            ok, reason = _reply_quality(reply)
            reason_counter[reason] += 1
            row = {
                "index": idx,
                "question": q,
                "reply": reply,
                "model": model,
                "ok": ok,
                "reason": reason,
            }
            rows.append(row)
            last_window.append(row)

            history.append(HistoryMessage(role="user", content=q))
            history.append(HistoryMessage(role="assistant", content=reply or "..."))
            if args.max_history == 0:
                history = []
            elif len(history) > args.max_history:
                history = history[-args.max_history :]

            if idx % 100 == 0:
                good = sum(1 for r in rows if r["ok"])
                print(f"progress {idx}/{args.max_questions} | ok={good} ({(good*100.0/idx):.2f}%)")

            if len(last_window) == last_window.maxlen and _window_is_human(list(last_window)):
                success_at = idx
                break
    finally:
        await manager.stop()
        await manager.close()

    return {
        "seed": args.seed,
        "max_questions": args.max_questions,
        "asked": len(rows),
        "window": args.window,
        "success": bool(success_at),
        "success_at": success_at,
        "speed_mode": bool(args.speed_mode),
        "reason_counter": dict(reason_counter),
        "rows": rows,
    }


def save_result(result: dict[str, Any], out_dir: str) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path = out / f"human_loop_{result['asked']}_{ts}.txt"
    json_path = out / f"human_loop_{result['asked']}_{ts}.json"

    lines: list[str] = []
    for row in result["rows"]:
        lines.append(f"Q{row['index']}: {row['question']}")
        lines.append(f"A{row['index']}: {row['reply']}")
        lines.append(f"M{row['index']}: {row['model']} | ok={row['ok']} | reason={row['reason']}")
        lines.append("")
    txt_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return txt_path, json_path


def main() -> int:
    if load_dotenv is not None:
        load_dotenv(".env", override=False)

    args = parse_args()
    result = asyncio.run(run_loop(args))
    txt_path, json_path = save_result(result, args.out_dir)

    print("Loop complete.")
    print(f"Asked: {result['asked']} / {result['max_questions']}")
    print(f"Window size: {result['window']}")
    print(f"Success: {result['success']} (at #{result['success_at']})")
    print(f"Reasons: {result['reason_counter']}")
    print(f"Transcript: {txt_path}")
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
