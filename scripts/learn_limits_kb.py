#!/usr/bin/env python3
"""
Batch-learn safety/limits topics from internet into local limits KB.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app_core.limits_learning import (  # noqa: E402
    bulk_learn_limits_questions,
    build_limits_seed_questions,
)
from app_core.settings import load_settings  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Learn limits KB from web")
    parser.add_argument("--count", type=int, default=80)
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="0 = auto-seed from current timestamp",
    )
    parser.add_argument("--timeout", type=int, default=14)
    parser.add_argument("--out-dir", default="transcripts")
    return parser.parse_args()


async def run() -> int:
    args = parse_args()
    if args.seed == 0:
        args.seed = int(dt.datetime.now().strftime("%y%m%d%H%M%S"))

    settings = load_settings()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    questions = build_limits_seed_questions(args.count, args.seed)
    report = await bulk_learn_limits_questions(
        questions,
        kb_path=settings.limits_kb_path,
        timeout_seconds=max(4, args.timeout),
        max_items=max(200, settings.limits_kb_max_items),
    )
    report["seed"] = args.seed
    report["count"] = args.count
    report["kb_path"] = str(settings.limits_kb_path).replace("\\", "/")

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = out_dir / f"limits_learn_report_{ts}.json"
    out_txt = out_dir / f"limits_learn_report_{ts}.txt"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"Seed: {report['seed']}",
        f"Count: {report['count']}",
        f"KB: {report['kb_path']}",
        f"Learned new: {report['learned_new']}",
        f"Served from KB: {report['served_from_kb']}",
        f"Failed: {report['failed']}",
    ]
    out_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Learned new: {report['learned_new']}")
    print(f"Served from KB: {report['served_from_kb']}")
    print(f"Failed: {report['failed']}")
    print(f"Report JSON: {out_json}")
    print(f"Report TXT: {out_txt}")
    return 0


if __name__ == "__main__":
    if load_dotenv is not None:
        load_dotenv(".env", override=False)
    raise SystemExit(asyncio.run(run()))

