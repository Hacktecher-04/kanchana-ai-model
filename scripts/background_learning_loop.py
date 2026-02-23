#!/usr/bin/env python3
"""
Continuous background learner for relationship KB + limits KB.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app_core.background_learning import run_background_learning_loop  # noqa: E402
from app_core.settings import load_settings  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run background learning loop")
    parser.add_argument("--sleep-minutes", type=int, default=None)
    parser.add_argument("--relationship-count", type=int, default=None)
    parser.add_argument("--limits-count", type=int, default=None)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    settings = load_settings()
    if args.sleep_minutes is not None:
        settings.background_learning_sleep_minutes = max(1, args.sleep_minutes)
    if args.relationship_count is not None:
        settings.background_relationship_count = max(0, args.relationship_count)
    if args.limits_count is not None:
        settings.background_limits_count = max(0, args.limits_count)
    await run_background_learning_loop(settings)
    return 0


if __name__ == "__main__":
    if load_dotenv is not None:
        load_dotenv(".env", override=False)
    raise SystemExit(asyncio.run(main()))

