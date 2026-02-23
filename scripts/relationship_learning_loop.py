#!/usr/bin/env python3
"""
Continuous background learner for relationship KB.

Use this as an optional worker process (e.g., Render background worker).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app_core.relationship_learning import (  # noqa: E402
    bulk_learn_relationship_questions,
    build_relationship_seed_questions,
)
from app_core.settings import load_settings  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuous relationship KB learner")
    parser.add_argument("--count-per-cycle", type=int, default=40)
    parser.add_argument("--sleep-minutes", type=int, default=180)
    parser.add_argument("--timeout", type=int, default=14)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    settings = load_settings()
    cycle = 0

    while True:
        cycle += 1
        seed = int(dt.datetime.now().strftime("%y%m%d%H%M%S")) + cycle
        questions = build_relationship_seed_questions(args.count_per_cycle, seed)
        report = await bulk_learn_relationship_questions(
            questions,
            kb_path=settings.relationship_kb_path,
            timeout_seconds=max(4, args.timeout),
            max_items=max(200, settings.relationship_kb_max_items),
        )
        print(
            (
                f"[cycle {cycle}] learned_new={report['learned_new']} "
                f"cached={report['served_from_kb']} failed={report['failed']} "
                f"kb={settings.relationship_kb_path}"
            ),
            flush=True,
        )
        await asyncio.sleep(max(60, args.sleep_minutes * 60))


if __name__ == "__main__":
    if load_dotenv is not None:
        load_dotenv(".env", override=False)
    raise SystemExit(asyncio.run(main()))
