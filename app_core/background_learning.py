from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

from .limits_learning import (
    bulk_learn_limits_questions,
    build_limits_seed_questions,
)
from .relationship_learning import (
    bulk_learn_relationship_questions,
    build_relationship_seed_questions,
)
from .settings import Settings


async def run_background_learning_loop(
    settings: Settings,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    cycle = 0
    sleep_seconds = max(60, settings.background_learning_sleep_minutes * 60)
    while True:
        cycle += 1
        seed = int(dt.datetime.now().strftime("%y%m%d%H%M%S")) + cycle
        rel_report: dict[str, Any] | None = None
        lim_report: dict[str, Any] | None = None

        if settings.enable_relationship_learning and settings.background_relationship_count > 0:
            try:
                rel_questions = build_relationship_seed_questions(
                    settings.background_relationship_count,
                    seed,
                )
                rel_report = await bulk_learn_relationship_questions(
                    rel_questions,
                    kb_path=settings.relationship_kb_path,
                    timeout_seconds=max(4, settings.relationship_learning_timeout_seconds),
                    max_items=max(200, settings.relationship_kb_max_items),
                )
            except Exception as exc:
                print(f"[bg-learn][cycle {cycle}] relationship error: {exc}", flush=True)

        if settings.enable_limits_guardrails and settings.background_limits_count > 0:
            try:
                lim_questions = build_limits_seed_questions(
                    settings.background_limits_count,
                    seed + 17,
                )
                lim_report = await bulk_learn_limits_questions(
                    lim_questions,
                    kb_path=settings.limits_kb_path,
                    timeout_seconds=max(4, settings.limits_learning_timeout_seconds),
                    max_items=max(200, settings.limits_kb_max_items),
                )
            except Exception as exc:
                print(f"[bg-learn][cycle {cycle}] limits error: {exc}", flush=True)

        rel_line = "relationship=disabled"
        if rel_report is not None:
            rel_line = (
                f"relationship(new={rel_report['learned_new']}, "
                f"cached={rel_report['served_from_kb']}, failed={rel_report['failed']})"
            )
        lim_line = "limits=disabled"
        if lim_report is not None:
            lim_line = (
                f"limits(new={lim_report['learned_new']}, "
                f"cached={lim_report['served_from_kb']}, failed={lim_report['failed']})"
            )
        print(f"[bg-learn][cycle {cycle}] {rel_line} | {lim_line}", flush=True)

        if stop_event is None:
            await asyncio.sleep(sleep_seconds)
            continue

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_seconds)
            if stop_event.is_set():
                break
        except TimeoutError:
            continue

