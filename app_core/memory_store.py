from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(text: str) -> str:
    low = re.sub(r"\s+", " ", (text or "").strip().lower())
    low = re.sub(r"[^a-z0-9 ]", "", low)
    return low[:220]


def _clean_line(text: str, *, max_len: int = 240) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if not compact:
        return ""
    if len(compact) > max_len:
        compact = compact[:max_len].strip()
    return compact


def _looks_noise(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return True
    if len(low.split()) < 3:
        return True
    if re.search(r"\b(hi|hello|hey|ok|okay|thanks|thank you|hmm)\b", low):
        return len(low.split()) <= 5
    if re.search(r"\b(app_api_key|secret|token|password|internal_api_key)\b", low):
        return True
    return False


def pick_memory_key(user_id: str | None, session_id: str | None) -> str:
    if user_id and user_id.strip():
        return f"user:{user_id.strip()[:120]}"
    if session_id and session_id.strip():
        return f"session:{session_id.strip()[:120]}"
    return ""


def _load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updated_at": _now_iso(), "users": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "updated_at": _now_iso(), "users": {}}
    if not isinstance(raw, dict):
        return {"version": 1, "updated_at": _now_iso(), "users": {}}
    users = raw.get("users")
    if not isinstance(users, dict):
        raw["users"] = {}
    return raw


def _save_store(path: Path, data: dict[str, Any]) -> None:
    data["updated_at"] = _now_iso()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_long_memories(
    path: Path,
    memory_key: str,
    *,
    query: str,
    read_items: int,
) -> list[str]:
    if not memory_key:
        return []
    with _LOCK:
        store = _load_store(path)
        users = store.get("users", {})
        entry = users.get(memory_key, {})
        items = entry.get("items", [])
        if not isinstance(items, list) or not items:
            return []

    # Lightweight lexical relevance + recency ordering.
    q_tokens = set(re.findall(r"[a-z0-9]{3,}", (query or "").lower()))
    scored: list[tuple[int, int, str]] = []
    for idx, text in enumerate(items):
        line = _clean_line(str(text))
        if not line:
            continue
        t_tokens = set(re.findall(r"[a-z0-9]{3,}", line.lower()))
        overlap = len(q_tokens & t_tokens)
        recency = idx  # larger index = newer
        scored.append((overlap, recency, line))

    if not scored:
        return []
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out: list[str] = []
    seen: set[str] = set()
    for _, _, line in scored:
        key = _norm(line)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= max(1, read_items):
            break
    return out


def add_long_memories(
    path: Path,
    memory_key: str,
    *,
    lines: list[str],
    max_users: int,
    max_items_per_user: int,
) -> int:
    if not memory_key or not lines:
        return 0
    prepared: list[str] = []
    for raw in lines:
        line = _clean_line(raw)
        if not line or _looks_noise(line):
            continue
        prepared.append(line)
    if not prepared:
        return 0

    with _LOCK:
        store = _load_store(path)
        users = store.setdefault("users", {})
        row = users.setdefault(memory_key, {"items": [], "updated_at": _now_iso()})
        existing = row.get("items", [])
        if not isinstance(existing, list):
            existing = []
        seen = {_norm(str(x)) for x in existing if str(x).strip()}
        added = 0
        for line in prepared:
            key = _norm(line)
            if not key or key in seen:
                continue
            existing.append(line)
            seen.add(key)
            added += 1
        if max_items_per_user > 0 and len(existing) > max_items_per_user:
            existing = existing[-max_items_per_user:]
        row["items"] = existing
        row["updated_at"] = _now_iso()
        users[memory_key] = row

        if max_users > 0 and len(users) > max_users:
            # Drop least recently updated memory buckets.
            ordered = sorted(
                users.items(),
                key=lambda x: str(x[1].get("updated_at", "")),
            )
            drop_n = len(users) - max_users
            for key, _ in ordered[:drop_n]:
                users.pop(key, None)

        store["users"] = users
        _save_store(path, store)
    return added

