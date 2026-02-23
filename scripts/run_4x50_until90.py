#!/usr/bin/env python3
"""
Run 4 successful batches of 50 questions each.

Rules:
- each batch must reach target ratio (default 90%)
- if a batch fails, retry with a fresh non-repeating 50-question set
- no question repeats across all attempts
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
    parser = argparse.ArgumentParser(description="4x50 until 90% campaign")
    parser.add_argument("--runs", type=int, default=4)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--target", type=float, default=90.0)
    parser.add_argument("--max-history", type=int, default=16)
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="0 = auto seed (different mix every run)",
    )
    parser.add_argument("--out-dir", default="transcripts")
    parser.add_argument("--max-attempts", type=int, default=50)
    return parser.parse_args()


def _normalize_q(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


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


def build_intents() -> list[str]:
    return [
        "mobile ka invention kisne kiya",
        "javascript ka roadmap bata",
        "javascript jante ho",
        "dance steps kaise sikhe",
        "convince karna ho to kya karu",
        "human psychology ka use answer me kaise hota hai",
        "reply late hota hai to fast kaise kare",
        "sun bhai ek bat bolu",
        "mere sath pehle acha response nahi aya",
        "mysterious reply do",
        "pineapple pizza pe opinion do",
        "random flow handle kar sakte ho",
        "ladki ko impress kaise kare respectfully",
        "start hi nahi hota to kya kare",
        "samajh nahi aa raha kaha se start karu",
        "unclear prompt handle kaise kare",
        "sarcasm use karna sahi hai kya",
        "one line motivation do",
        "weekend productive kaise rahe",
        "job quit decision ka rule kya",
        "space me sound travel kyu nahi karta",
        "python start kaise kare beginner",
        "recursion simple words me samjha",
        "caching ka use kya hai",
        "api secure kaise kare",
        "bug report ka sahi format",
        "prompt writing better kaise kare",
        "deployment checklist do",
        "connection refused fix kaise kare",
        "server response slow ho to kya check kare",
        "startup command kya hai",
        "qwen aur mistral compare karo",
        "exam plan short do",
        "budget salary planning basic do",
        "discipline build ka realistic plan do",
        "confidence aur arrogance me difference",
        "confidence build karne ka first step",
        "productivity improve ka fastest tareeka",
        "startup idea low budget me",
        "multitasking acha hai ya nahi",
        "mind blank ho to restart kaise kare",
        "sleep improve ka short routine do",
        "interview anxiety ka quick fix do",
        "rejection handle kaise kare",
        "whole chat ka concise summary do",
        "apology text ka format do",
        "ek puzzle do",
        "ek micro story do",
        "travel tips short do",
        "last line strong closing do",
        "din kharab ho to practical tip do",
        "kaise ho",
        "hello bhai",
        "kuch bolo",
        "aaj din kaisa tha",
        "mere bare me kya pata hai",
        "kuch useful batao",
        "ajeeb lag raha hai kya karu",
        "acha theek hai ab bolo",
        "no nothing, now what",
        "help chahiye",
        "problem fix karna hai",
        "summary kaise likhe long text ka",
        "startup idea ka first customer kaise milega",
        "discipline break ho jaye to comeback kaise kare",
        "interview ke liye short confidence line do",
        "productivity aur rest ko balance kaise kare",
        "memory continuity kaise maintain hoti hai",
        "calm tone me answer do",
        "random question bhi handle kar paoge",
        "agar answer na pata ho to kya karoge",
        "response quality improve kaise karte ho",
        "generic reply avoid kaise kare",
        "api key secure rakhne ka best way",
        "debugging start kaha se kare",
        "prompt me clarity lane ka best rule",
        "server startup verify kaise kare",
        "deployment ke baad health check kaise kare",
        "error logs se root cause kaise nikale",
    ]


def build_templates() -> list[str]:
    return [
        "{intent}",
        "bhai {intent}",
        "yar {intent}",
        "{intent} short me batao",
        "{intent} practical answer do",
        "please {intent}",
        "mujhe {intent} samjhao",
        "{intent} ek line me",
        "{intent} simple words me",
        "quickly bolo: {intent}",
        "{intent} without bakwas",
        "{intent} direct jawab do",
        "seedha bolo {intent}",
        "{intent} roman hindi me",
    ]


def build_suffixes() -> list[str]:
    return [
        "",
        " abhi",
        " jaldi",
        " step by step",
        " practical tarike se",
        " short and clear",
        " example ke saath",
        " please",
    ]


def build_question_pool() -> list[str]:
    intents = build_intents()
    templates = build_templates()
    suffixes = build_suffixes()
    out: list[str] = []
    seen: set[str] = set()
    for intent in intents:
        for temp in templates:
            for suf in suffixes:
                q = f"{temp.format(intent=intent)}{suf}".strip()
                n = _normalize_q(q)
                if n in seen:
                    continue
                seen.add(n)
                out.append(q)
    return out


def pick_unique_batch(
    pool: list[str],
    used: set[str],
    count: int,
    rng: random.Random,
) -> list[str]:
    available = [q for q in pool if _normalize_q(q) not in used]
    rng.shuffle(available)
    chosen = available[:count]
    for q in chosen:
        used.add(_normalize_q(q))
    return chosen


async def run_batch(
    *,
    run_no: int,
    attempt_no: int,
    questions: list[str],
    max_history: int,
    out_dir: Path,
    chat_fn: Any,
    chat_request_cls: Any,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    history: list[dict[str, str]] = []
    good = 0
    errors = 0
    web_hits = 0

    for idx, q in enumerate(questions, start=1):
        try:
            payload = chat_request_cls(message=q, history=history)
            result = await chat_fn(payload, _="campaign-4x50")
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
    txt_file = out_dir / f"run{run_no}_attempt{attempt_no}_{ts}.txt"
    json_file = out_dir / f"run{run_no}_attempt{attempt_no}_{ts}.json"

    lines: list[str] = []
    for row in rows:
        lines.append(f"Q{row['index']}: {row['question']}")
        lines.append(f"A{row['index']}: {row['reply']}")
        lines.append(f"M{row['index']}: {row['model']}")
        lines.append(f"GOOD{row['index']}: {row['is_good']}")
        lines.append("")
    txt_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    payload = {
        "run_no": run_no,
        "attempt_no": attempt_no,
        "total": len(questions),
        "good": good,
        "ratio_100": ratio,
        "errors": errors,
        "web_fallback_hits": web_hits,
        "txt_file": str(txt_file).replace("\\", "/"),
        "rows": rows,
    }
    json_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    payload["json_file"] = str(json_file).replace("\\", "/")
    return payload


async def run_campaign(args: argparse.Namespace) -> dict[str, Any]:
    from app_core.api import chat as chat_fn, manager as manager_obj
    from app_core.schemas import ChatRequest as ChatRequestCls

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pool = build_question_pool()
    used: set[str] = set()
    rng = random.Random(args.seed)
    run_summaries: list[dict[str, Any]] = []

    await manager_obj.start()
    try:
        for run_no in range(1, args.runs + 1):
            success = False
            attempts: list[dict[str, Any]] = []
            for attempt_no in range(1, args.max_attempts + 1):
                batch = pick_unique_batch(pool, used, args.count, rng)
                if len(batch) < args.count:
                    raise RuntimeError(
                        "Question pool exhausted before campaign completed."
                    )
                result = await run_batch(
                    run_no=run_no,
                    attempt_no=attempt_no,
                    questions=batch,
                    max_history=args.max_history,
                    out_dir=out_dir,
                    chat_fn=chat_fn,
                    chat_request_cls=ChatRequestCls,
                )
                result["passed"] = result["ratio_100"] >= args.target
                attempts.append(result)
                if result["passed"]:
                    success = True
                    break

            run_summaries.append(
                {
                    "run_no": run_no,
                    "target_ratio_100": args.target,
                    "success": success,
                    "attempts": attempts,
                    "attempt_count": len(attempts),
                }
            )
            if not success:
                break
    finally:
        await manager_obj.stop()
        await manager_obj.close()

    all_questions: list[str] = []
    for run in run_summaries:
        for att in run["attempts"]:
            all_questions.extend([row["question"] for row in att["rows"]])
    norm = [_normalize_q(q) for q in all_questions]
    dup_count = len(norm) - len(set(norm))

    summary = {
        "seed": args.seed,
        "runs_target": args.runs,
        "count_per_batch": args.count,
        "target_ratio_100": args.target,
        "max_history": args.max_history,
        "duplicate_questions_global": dup_count,
        "total_questions_asked": len(all_questions),
        "runs": run_summaries,
    }
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_file = out_dir / f"campaign_4x50_summary_{ts}.json"
    summary_file.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary["summary_file"] = str(summary_file).replace("\\", "/")
    return summary


def main() -> int:
    if load_dotenv is not None:
        load_dotenv(".env", override=False)
    args = parse_args()
    if args.seed == 0:
        args.seed = int(dt.datetime.now().strftime("%y%m%d%H%M%S"))
    result = asyncio.run(run_campaign(args))

    print(f"Seed: {result['seed']}")
    print(
        f"Global duplicate questions: {result['duplicate_questions_global']} "
        f"(total asked: {result['total_questions_asked']})"
    )
    for run in result["runs"]:
        status = "PASS" if run["success"] else "FAIL"
        print(f"Run {run['run_no']}: {status} after {run['attempt_count']} attempts")
        for att in run["attempts"]:
            print(
                f"  Attempt {att['attempt_no']}: "
                f"{att['good']}/{att['total']} good ({att['ratio_100']}%), "
                f"errors={att['errors']}, passed={att['passed']}"
            )
            print(f"    TXT: {att['txt_file']}")
            print(f"    JSON: {att['json_file']}")
    print(f"Summary: {result['summary_file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

