#!/usr/bin/env python3
"""
Analyze a saved chat transcript from scripts/chat_20_terminal.py.

Default input format per line:
  you: <text>
  ai: <text>
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import re
from collections import Counter
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze chat transcript quality")
    parser.add_argument("--input-file", default="")
    parser.add_argument("--output-dir", default="transcripts")
    return parser.parse_args()


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _latest_transcript(path: Path) -> Path:
    files = sorted(path.glob("chat_20_*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("No chat_20_*.txt transcript found in transcripts/")
    return files[0]


def _is_hindi_like(text: str) -> bool:
    low = text.lower()
    return bool(
        re.search(
            (
                r"\b(bhai|kaise|kya|nahi|haan|tum|mujhe|hai|ho|kar|kr|"
                r"samajh|bata|chahiye|raha|rahi|yaar|theek|acha|sirf)\b"
            ),
            low,
        )
    )


def _english_word_count(text: str) -> int:
    return len(
        re.findall(
            r"\b(i|you|your|my|the|and|is|are|what|how|why|please|can|could|would)\b",
            text.lower(),
        )
    )


def load_rows(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("you:"):
            rows.append(("you", line[4:].strip()))
        elif line.lower().startswith("ai:"):
            rows.append(("ai", line[3:].strip()))
    return rows


def analyze(rows: list[tuple[str, str]]) -> dict:
    users = [text for role, text in rows if role == "you"]
    ais = [text for role, text in rows if role == "ai"]

    normalized_ai = [_normalize(x) for x in ais]
    counts = Counter(normalized_ai)
    duplicate_ai_count = sum(c - 1 for c in counts.values() if c > 1)
    top_duplicates = [
        {"count": c, "reply": txt}
        for txt, c in counts.items()
        if c > 1
    ]
    top_duplicates.sort(key=lambda x: x["count"], reverse=True)
    top_duplicates = top_duplicates[:5]

    max_similarity = 0.0
    max_pair: tuple[str, str] = ("", "")
    for i in range(len(ais)):
        for j in range(i + 1, len(ais)):
            sim = difflib.SequenceMatcher(None, normalized_ai[i], normalized_ai[j]).ratio()
            if sim > max_similarity:
                max_similarity = sim
                max_pair = (ais[i], ais[j])

    generic_patterns = [
        r"\b(seedha pucho|jo puchna hai|what can i assist|i am here to help)\b",
        r"\b(let me know if there is anything else|glad to help)\b",
    ]
    generic_hits: list[dict] = []
    for idx, msg in enumerate(ais, start=1):
        low = msg.lower()
        if any(re.search(p, low) for p in generic_patterns):
            generic_hits.append({"turn": idx, "reply": msg})

    short_ai = sum(1 for x in ais if len(x.split()) <= 3)

    language_mismatch_turns: list[int] = []
    for idx, (u, a) in enumerate(zip(users, ais), start=1):
        if _is_hindi_like(u):
            if _english_word_count(a) >= 8 and not _is_hindi_like(a):
                language_mismatch_turns.append(idx)

    score = 100
    score -= duplicate_ai_count * 6
    score -= len(generic_hits) * 5
    score -= len(language_mismatch_turns) * 4
    if max_similarity > 0.90:
        score -= 8
    if short_ai > 2:
        score -= (short_ai - 2) * 2
    score = max(0, min(100, score))

    return {
        "summary": {
            "user_turns": len(users),
            "ai_turns": len(ais),
            "duplicate_ai_count": duplicate_ai_count,
            "max_ai_pair_similarity": round(max_similarity, 4),
            "short_ai_replies_le_3_words": short_ai,
            "generic_reply_hits": len(generic_hits),
            "language_mismatch_hits": len(language_mismatch_turns),
            "quality_score_100": score,
        },
        "top_duplicate_ai_replies": top_duplicates,
        "max_similarity_pair": {"a": max_pair[0], "b": max_pair[1]},
        "generic_hits": generic_hits,
        "language_mismatch_turns": language_mismatch_turns,
    }


def save_report(out_dir: Path, transcript_path: Path, report: dict) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"chat_report_{ts}.json"
    txt_path = out_dir / f"chat_report_{ts}.txt"

    payload = {
        "transcript_file": str(transcript_path),
        "timestamp": ts,
        **report,
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    s = report["summary"]
    lines = [
        f"Transcript: {transcript_path}",
        f"User turns: {s['user_turns']}",
        f"AI turns: {s['ai_turns']}",
        f"Quality score (0-100): {s['quality_score_100']}",
        f"Duplicate AI replies: {s['duplicate_ai_count']}",
        f"Max AI pair similarity: {s['max_ai_pair_similarity']}",
        f"Generic reply hits: {s['generic_reply_hits']}",
        f"Language mismatch hits: {s['language_mismatch_hits']}",
        "",
        "Top duplicate replies:",
    ]
    for item in report["top_duplicate_ai_replies"]:
        lines.append(f"- [{item['count']}x] {item['reply']}")
    if not report["top_duplicate_ai_replies"]:
        lines.append("- none")
    lines.append("")
    lines.append("Potential generic hits:")
    for item in report["generic_hits"]:
        lines.append(f"- turn {item['turn']}: {item['reply']}")
    if not report["generic_hits"]:
        lines.append("- none")

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, txt_path


def main() -> int:
    args = parse_args()
    out_dir = Path(args.output_dir)

    input_file = (args.input_file or "").strip()
    if input_file:
        transcript = Path(input_file)
        if not transcript.exists():
            raise FileNotFoundError(f"Input transcript not found: {transcript}")
    else:
        transcript = _latest_transcript(out_dir)

    rows = load_rows(transcript)
    if not rows:
        raise RuntimeError("Transcript has no parseable rows.")

    report = analyze(rows)
    json_path, txt_path = save_report(out_dir, transcript, report)

    summary = report["summary"]
    print(f"Analyzed transcript: {transcript}")
    print(
        "Quality score: "
        f"{summary['quality_score_100']} | duplicates: {summary['duplicate_ai_count']} | "
        f"max_sim: {summary['max_ai_pair_similarity']}"
    )
    print(f"Saved JSON: {json_path}")
    print(f"Saved TXT:  {txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

