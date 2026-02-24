from __future__ import annotations

import math
import re
from functools import lru_cache

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_VEC_DIM = 1024


def _hash_token(token: str) -> int:
    h = 2166136261
    for b in token.encode("utf-8", errors="ignore"):
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    return h % _VEC_DIM


@lru_cache(maxsize=50000)
def text_to_vector(text: str) -> tuple[tuple[int, float], ...]:
    low = (text or "").lower()
    toks = _TOKEN_RE.findall(low)
    if not toks:
        return ()

    weights: dict[int, float] = {}
    prev = ""
    for tok in toks:
        idx = _hash_token(tok)
        weights[idx] = weights.get(idx, 0.0) + 1.0
        if prev:
            bi = _hash_token(prev + "_" + tok)
            weights[bi] = weights.get(bi, 0.0) + 0.45
        prev = tok

    norm = math.sqrt(sum(v * v for v in weights.values()))
    if norm <= 0:
        return ()
    out = sorted((idx, val / norm) for idx, val in weights.items())
    return tuple(out)


def cosine_similarity(
    left: tuple[tuple[int, float], ...],
    right: tuple[tuple[int, float], ...],
) -> float:
    if not left or not right:
        return 0.0
    i = 0
    j = 0
    dot = 0.0
    while i < len(left) and j < len(right):
        li, lv = left[i]
        ri, rv = right[j]
        if li == ri:
            dot += lv * rv
            i += 1
            j += 1
            continue
        if li < ri:
            i += 1
        else:
            j += 1
    if dot < 0:
        return 0.0
    if dot > 1:
        return 1.0
    return dot

