#!/usr/bin/env python3
"""
Run quality campaign:
1) 50-question batch
2) check quality ratio
3) if < target, switch to upgraded prompt bank and run next 50 with no repeats
4) if still < target, run one more upgraded 50 with no repeats
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


GENERIC_RE = re.compile(
    (
        r"\b(topic do|sawal clear bhejo|exact point batao|seedha pucho|"
        r"short aur direct jawab dunga|share one specific point|drop one clear question|"
        r"specific prompt)\b"
    ),
    re.IGNORECASE,
)

MOJIBAKE_RE = re.compile(r"(\u00c3|\u00c2|\u00e2|\ufffd)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run 90% quality campaign")
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--target", type=float, default=90.0)
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Random seed. Use 0 for auto-seed (new question mix every run).",
    )
    parser.add_argument("--max-history", type=int, default=16)
    parser.add_argument("--out-dir", default="transcripts")
    return parser.parse_args()


def _normalize_q(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def build_base_bank() -> list[str]:
    return [
        "agar mai upset hoon to quick calm kaise karu",
        "ek startup idea do low budget me",
        "bhai interview me first impression kaise achha kare",
        "top 3 habits for students batao",
        "kaise pata chale answer correct hai ya guess",
        "distance between earth and moon kitna hota hai",
        "SQL kya hota hai beginner level me batao",
        "how to avoid procrastination quickly",
        "how to become better at problem solving",
        "agar fail ho jaye to comeback kaise kare",
        "what is cloud computing simple explanation",
        "agar mujhe confidence build karna ho to first step kya",
        "mujhe negotiation sikhao one tip",
        "tumhari strongest skill kya hai",
        "ek subtle playful line do",
        "ek line me explain karo critical thinking",
        "bhai ek light joke suna",
        "penicillin kisne discover kiya",
        "internet se batao latest gold price trend",
        "agar neend nahi aaye to kya karu",
        "resume me sabse common galti kya hoti hai",
        "how to learn JavaScript in 30 days roadmap",
        "tell me one book for better thinking",
        "give one practical confidence exercise",
        "ek line me photosynthesis samjhao",
        "who discovered gravity",
        "kya tum coding debug kar sakte ho",
        "who is founder of Microsoft",
        "agar answer nahi pata ho to tum kya karte ho",
        "kaise decide kare ki kaun sa career choose karna chahiye",
        "agar koi bole tum boring ho to kya bolu",
        "bhai mujhe discipline build karna hai, plan do",
        "productivity ke liye pomodoro useful hai kya",
        "ek calm reply do jab stress high ho",
        "ek choti si sales pitch line banao",
        "bhai mujhe smart tareeke se padhna hai",
        "time management ke 3 practical tips do",
        "aaj ka ek short motivational line do",
        "agar koi rude ho to respond kaise kare",
        "ghar pe English speaking improve kaise karu",
        "healthy morning routine ka sample do",
        "what is quantum entanglement in one line",
        "bitcoin kya hota hai simple words me",
        "networking skill kaise improve karte hain",
        "git branch ka use kya hota hai",
        "roman hindi me short reply do, AI kya hota hai",
        "internet source ke sath batao mars par life possible hai kya",
        "kya tum internet se latest info la sakte ho",
        "best way to remember what I study",
        "kya tum meri hindi aur english mix samajh sakte ho",
    ]


def build_upgraded_bank() -> list[str]:
    anchors = [
        "mobile ka invention kisne kiya",
        "javascript ka roadmap bata",
        "javascript jante ho",
        "dance steps kaise sikhe",
        "convince karna ho to kya karu",
        "human psychology ka use answer me kaise hota hai",
        "reply late hota hai to fast kaise kare",
        "sun bhai ek bat bolu",
        "mere sath pehle acha response nahi aya",
        "confidence aur arrogance me farq",
        "discipline build karne ka plan",
        "productivity improve karne ka tareeka",
        "startup idea low budget me",
        "mind blank ho to kya kare",
        "sleep improve ka short plan",
        "interview anxiety ka quick fix",
        "rejection handle kaise kare",
        "python start kaise kare",
        "recursion simple way me samjha",
        "caching ka use kya hai",
        "api secure kaise kare",
        "bug report ka sahi format",
        "prompt writing better kaise kare",
        "deployment checklist do",
        "connection refused fix kaise kare",
        "startup command kya hai",
        "qwen aur mistral compare karo",
        "travel tips short do",
        "one-line motivation do",
        "weekend productive kaise rahe",
        "job quit decision ka rule",
        "summary kaise likhe",
        "apology text ka format",
        "puzzle do",
        "micro story do",
        "sarcasm use karna sahi hai kya",
        "mysterious reply do",
        "pineapple pizza pe opinion",
        "random flow handle kar sakte ho",
        "samajh nahi aa raha kaha se start karu",
        "unclear prompt handle kaise kare",
        "server response slow ho to kya check kare",
        "calm tone me reply do",
        "memory continuity kaise maintain hoti hai",
        "exam plan short do",
        "budget plan basic do",
        "multitasking acha hai kya",
        "last line strong closing do",
        "din kharab ho to ek practical tip",
        "kaise ho",
        "hello bhai",
        "kuch bolo",
        "aaj ka din kaisa tha",
        "help chahiye",
        "problem fix karna hai",
    ]
    templates = [
        "bhai {a}",
        "{a} short me batao",
        "{a} practical answer do",
        "mujhe {a} samjhao",
    ]
    out: list[str] = []
    seen: set[str] = set()
    for a in anchors:
        for t in templates:
            q = t.format(a=a).strip()
            n = _normalize_q(q)
            if n in seen:
                continue
            seen.add(n)
            out.append(q)
    return out


def select_questions(
    bank: list[str], used: set[str], count: int, seed: int
) -> list[str]:
    rng = random.Random(seed)
    available = [q for q in bank if _normalize_q(q) not in used]
    rng.shuffle(available)
    picked = available[:count]
    for q in picked:
        used.add(_normalize_q(q))
    return picked


def is_good_reply(reply: str, model: str) -> bool:
    text = (reply or "").strip()
    low = text.lower()
    if not text:
        return False
    if model == "request-error":
        return False
    if text.startswith("[ERROR]"):
        return False
    if GENERIC_RE.search(low):
        return False
    if MOJIBAKE_RE.search(text):
        return False
    if len(text.split()) < 4:
        return False
    return True


async def run_batch(
    *,
    questions: list[str],
    max_history: int,
    label: str,
    out_dir: Path,
    batch_index: int,
    chat_fn: Any,
    chat_request_cls: Any,
) -> dict[str, Any]:
    history: list[dict[str, str]] = []
    rows: list[dict[str, Any]] = []
    good = 0
    errors = 0
    web_hits = 0

    for idx, q in enumerate(questions, start=1):
        try:
            payload = chat_request_cls(message=q, history=history)
            result = await chat_fn(payload, _="campaign")
            reply = (result.reply or "").strip()
            model = str(result.model or "").strip()
        except Exception as exc:
            reply = f"[ERROR] {exc}"
            model = "request-error"
            errors += 1

        if model == "web-fallback":
            web_hits += 1
        ok = is_good_reply(reply, model)
        if ok:
            good += 1

        rows.append(
            {
                "index": idx,
                "question": q,
                "reply": reply,
                "model": model,
                "is_good": ok,
            }
        )

        history.append({"role": "user", "content": q})
        history.append({"role": "assistant", "content": reply})
        if max_history == 0:
            history = []
        elif len(history) > max_history:
            history = history[-max_history:]

    ratio = round((good * 100.0) / max(1, len(questions)), 2)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_path = out_dir / f"campaign_batch_{batch_index}_{label}_{ts}.txt"
    json_path = out_dir / f"campaign_batch_{batch_index}_{label}_{ts}.json"

    lines: list[str] = []
    for row in rows:
        lines.append(f"Q{row['index']}: {row['question']}")
        lines.append(f"A{row['index']}: {row['reply']}")
        lines.append(f"M{row['index']}: {row['model']}")
        lines.append(f"GOOD{row['index']}: {row['is_good']}")
        lines.append("")
    txt_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    payload = {
        "batch_index": batch_index,
        "label": label,
        "total": len(questions),
        "good": good,
        "ratio_100": ratio,
        "errors": errors,
        "web_fallback_hits": web_hits,
        "txt_file": str(txt_path).replace("\\", "/"),
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload["json_file"] = str(json_path).replace("\\", "/")
    return payload


async def run_campaign(args: argparse.Namespace) -> dict[str, Any]:
    from app_core.api import chat as chat_fn, manager as manager_obj
    from app_core.schemas import ChatRequest as ChatRequestCls

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    used_questions: set[str] = set()
    base_bank = build_base_bank()
    upgraded_bank = build_upgraded_bank()
    batches: list[dict[str, Any]] = []

    await manager_obj.start()
    try:
        # Batch 1: base run
        q1 = select_questions(base_bank, used_questions, args.count, args.seed)
        b1 = await run_batch(
            questions=q1,
            max_history=args.max_history,
            label="base",
            out_dir=out_dir,
            batch_index=1,
            chat_fn=chat_fn,
            chat_request_cls=ChatRequestCls,
        )
        batches.append(b1)

        if b1["ratio_100"] >= args.target:
            # Batch 2: another 50 different questions (same mode)
            q2 = select_questions(base_bank, used_questions, args.count, args.seed + 1)
            if len(q2) < args.count:
                q2 = select_questions(upgraded_bank, used_questions, args.count, args.seed + 101)
            b2 = await run_batch(
                questions=q2,
                max_history=args.max_history,
                label="base-second",
                out_dir=out_dir,
                batch_index=2,
                chat_fn=chat_fn,
                chat_request_cls=ChatRequestCls,
            )
            batches.append(b2)
        else:
            # Upgrade path
            q2 = select_questions(upgraded_bank, used_questions, args.count, args.seed + 101)
            b2 = await run_batch(
                questions=q2,
                max_history=min(args.max_history, 16),
                label="upgraded",
                out_dir=out_dir,
                batch_index=2,
                chat_fn=chat_fn,
                chat_request_cls=ChatRequestCls,
            )
            batches.append(b2)

            if b2["ratio_100"] < args.target:
                # One more upgraded check with different 50 questions
                q3 = select_questions(upgraded_bank, used_questions, args.count, args.seed + 202)
                b3 = await run_batch(
                    questions=q3,
                    max_history=min(args.max_history, 16),
                    label="upgraded-second",
                    out_dir=out_dir,
                    batch_index=3,
                    chat_fn=chat_fn,
                    chat_request_cls=ChatRequestCls,
                )
                batches.append(b3)
    finally:
        await manager_obj.stop()
        await manager_obj.close()

    final = {
        "target_ratio_100": args.target,
        "count_per_batch": args.count,
        "max_history": args.max_history,
        "seed": args.seed,
        "batches": batches,
    }
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    final_path = out_dir / f"campaign_summary_{ts}.json"
    final_path.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    final["summary_file"] = str(final_path).replace("\\", "/")
    return final


def main() -> int:
    if load_dotenv is not None:
        load_dotenv(".env", override=False)
    args = parse_args()
    if args.seed == 0:
        args.seed = int(dt.datetime.now().strftime("%y%m%d%H%M%S"))
    result = asyncio.run(run_campaign(args))
    print(f"Target ratio: {result['target_ratio_100']}")
    for b in result["batches"]:
        print(
            f"Batch {b['batch_index']} [{b['label']}]: "
            f"{b['good']}/{b['total']} good ({b['ratio_100']}%), errors={b['errors']}"
        )
        print(f"  TXT: {b['txt_file']}")
        print(f"  JSON: {b['json_file']}")
    print(f"Summary: {result['summary_file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
