from __future__ import annotations

import html
import json
import random
import re
import difflib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

DEFAULT_LIMITS_KB_PATH = Path("knowledge_limits.json")

_ALLOWED_DOMAINS = {
    "who.int",
    "cdc.gov",
    "nimh.nih.gov",
    "samhsa.gov",
    "988lifeline.org",
    "mayoclinic.org",
    "apa.org",
    "loveisrespect.org",
    "rainn.org",
    "plannedparenthood.org",
    "healthline.com",
    "verywellmind.com",
    "nhs.uk",
    "unicef.org",
    "helpguide.org",
    "mind.org.uk",
    "medlineplus.gov",
    "clevelandclinic.org",
    "webmd.com",
}

_RISK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "self_harm",
        re.compile(
            (
                r"\b(suicide|suicidal|kill myself|end my life|self harm|self-harm|"
                r"want to die|die today|hurt myself|jaan dena|marna hai|khud ko nuksan)\b"
            ),
            re.IGNORECASE,
        ),
    ),
    (
        "abuse_violence",
        re.compile(
            (
                r"\b(domestic violence|abuse|abusive partner|hit me|stalking|harassment|"
                r"forced relationship|threatening partner|threatening me|hitting me|"
                r"rape|sexual assault|partner marta hai|dhamki deta hai)\b"
            ),
            re.IGNORECASE,
        ),
    ),
    (
        "medical_crisis",
        re.compile(
            (
                r"\b(chest pain|overdose|poison|severe bleeding|cant breathe|can't breathe|"
                r"panic attack severe|fainting|emergency symptoms)\b"
            ),
            re.IGNORECASE,
        ),
    ),
]

_STOPWORDS = {
    "how",
    "what",
    "when",
    "where",
    "why",
    "which",
    "best",
    "way",
    "guide",
    "with",
    "for",
    "and",
    "the",
    "to",
    "in",
    "of",
    "on",
    "a",
    "an",
    "is",
    "are",
    "can",
    "should",
    "would",
    "after",
    "before",
    "without",
    "between",
    "safety",
    "limits",
    "human",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def detect_high_risk_category(text: str) -> str:
    low = (text or "").strip().lower()
    if not low:
        return ""
    for category, pattern in _RISK_PATTERNS:
        if pattern.search(low):
            return category
    return ""


def _normalize_key(text: str) -> str:
    low = re.sub(r"\s+", " ", (text or "").strip().lower())
    low = re.sub(r"[^a-z0-9 ?!.,-]", "", low)
    return low[:320]


def _tokens(text: str) -> set[str]:
    raw = set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))
    return {t for t in raw if t not in _STOPWORDS}


def _extract_domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().replace("www.", "")
    except Exception:
        return ""


def _load_kb(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "updated_at": _now_iso(), "items": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "updated_at": _now_iso(), "items": []}
    if not isinstance(raw, dict):
        return {"version": 1, "updated_at": _now_iso(), "items": []}
    items = raw.get("items")
    if not isinstance(items, list):
        raw["items"] = []
    return raw


def _save_kb(path: Path, kb: dict[str, Any]) -> None:
    kb["updated_at"] = _now_iso()
    path.write_text(json.dumps(kb, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _looks_mojibake(text: str) -> bool:
    if not text:
        return False
    return any(
        token in text
        for token in (
            "\ufffd",
            "\u00c3",
            "\u00c2",
            "\u00e2",
        )
    )


def _trim(text: str, max_len: int = 360) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if len(compact) <= max_len:
        return compact
    cut = compact[:max_len]
    if "." in cut:
        cut = cut.rsplit(".", 1)[0].strip()
    return cut


def _sanitize_snippet(text: str) -> str:
    s = re.sub(r"\s+", " ", (text or "").strip())
    s = re.sub(r"\s+Source:\s*https?://\S+.*$", "", s, flags=re.I)
    if _looks_mojibake(s):
        return ""
    return _trim(s, 340)


def _format_limits_answer(snippet: str, source: str, lang_mode: str) -> str:
    clean = _sanitize_snippet(snippet)
    if lang_mode == "hi":
        return f"Safety guidance ke hisab se: {clean} Source: {source}"
    return f"From safety guidance: {clean} Source: {source}"


def safety_notice_for_category(category: str, lang_mode: str) -> str:
    if not category:
        return ""
    if lang_mode == "hi":
        if category == "self_harm":
            return (
                "Agar immediate danger ho to turant local emergency services ko call karo. "
                "US me 988 call/text support available hai."
            )
        if category in {"abuse_violence", "medical_crisis"}:
            return (
                "Agar turant danger ho to emergency services ko call karo. "
                "Safety pehle rakho."
            )
    else:
        if category == "self_harm":
            return (
                "If there is immediate danger, contact local emergency services now. "
                "In the U.S., call or text 988."
            )
        if category in {"abuse_violence", "medical_crisis"}:
            return (
                "If there is immediate danger, contact emergency services right away. "
                "Prioritize immediate safety."
            )
    return ""


def _unwrap_ddg_link(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if "duckduckgo.com" in (parsed.netloc or "").lower() and parsed.path.startswith("/l/"):
        uds = parse_qs(parsed.query).get("uddg", [])
        if uds:
            return unquote(uds[0]).strip()
    return url


def _parse_ddg_html_candidates(page: str) -> list[tuple[str, str]]:
    links = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippets = re.findall(
        r'<(?:a|span)[^>]*class="result__snippet"[^>]*>(.*?)</(?:a|span)>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    out: list[tuple[str, str]] = []
    for idx, row in enumerate(links):
        href_raw, _title = row
        url = _unwrap_ddg_link(href_raw)
        snippet_html = snippets[idx] if idx < len(snippets) else ""
        snippet = re.sub(r"<[^>]+>", " ", snippet_html)
        snippet = html.unescape(snippet)
        snippet = re.sub(r"\s+", " ", snippet).strip()
        if not url or not snippet:
            continue
        out.append((snippet, url))
    return out


async def _search_limits_web(question: str, timeout_seconds: int) -> tuple[str, str]:
    headers = {
        "User-Agent": "limits-learner/1.0 (+https://localhost)",
        "Accept-Language": "en-US,en;q=0.8",
    }
    async with httpx.AsyncClient(
        timeout=max(4, timeout_seconds),
        headers=headers,
        follow_redirects=True,
    ) as client:
        resp = await client.get("https://duckduckgo.com/html/", params={"q": question})
        resp.raise_for_status()
        cands = _parse_ddg_html_candidates(resp.text)

    if not cands:
        return "", ""

    trusted = [
        (snippet, url)
        for snippet, url in cands
        if _extract_domain(url) in _ALLOWED_DOMAINS
    ]
    if not trusted:
        return "", ""
    best_snippet, best_url = trusted[0]
    return _trim(best_snippet, 340), best_url


def _score_item(question: str, msg_tokens: set[str], item: dict[str, Any]) -> float:
    q_text = str(item.get("question", ""))
    q_tokens = _tokens(q_text)
    a_tokens = _tokens(str(item.get("answer", "")))
    if not msg_tokens:
        return 0.0
    overlap_q = len(msg_tokens & q_tokens)
    overlap_all = len(msg_tokens & (q_tokens | a_tokens))
    if overlap_q == 0 and overlap_all == 0:
        return 0.0
    q_cov = overlap_q / float(len(msg_tokens))
    all_cov = overlap_all / float(len(msg_tokens))
    if len(msg_tokens) >= 2 and q_cov < 0.30 and all_cov < 0.45:
        return 0.0
    sim = difflib.SequenceMatcher(
        None,
        _normalize_key(question),
        _normalize_key(q_text),
    ).ratio()
    return (q_cov * 0.65) + (all_cov * 0.25) + (sim * 0.10)


def get_limits_answer(
    question: str,
    lang_mode: str,
    *,
    kb_path: Path,
    min_score: float = 0.48,
    exact_only: bool = False,
) -> tuple[str, str]:
    kb = _load_kb(kb_path)
    items = kb.get("items", [])
    if not isinstance(items, list) or not items:
        return "", ""
    key = _normalize_key(question)
    msg_tokens = _tokens(question)

    for item in reversed(items):
        if _normalize_key(str(item.get("question", ""))) == key:
            ans = str(item.get("answer", "")).strip()
            src = str(item.get("source", "")).strip()
            dom = _extract_domain(src)
            if dom and dom not in _ALLOWED_DOMAINS:
                continue
            if ans and not _looks_mojibake(ans):
                return _format_limits_answer(ans, src, lang_mode), src

    if exact_only:
        return "", ""

    scored: list[tuple[float, dict[str, Any]]] = []
    for item in items:
        s = _score_item(question, msg_tokens, item)
        if s <= 0:
            continue
        scored.append((s, item))
    if not scored:
        return "", ""
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top_item = scored[0]
    if top_score < min_score:
        return "", ""
    ans = str(top_item.get("answer", "")).strip()
    src = str(top_item.get("source", "")).strip()
    dom = _extract_domain(src)
    if dom and dom not in _ALLOWED_DOMAINS:
        return "", ""
    if not ans or _looks_mojibake(ans):
        return "", ""
    return _format_limits_answer(ans, src, lang_mode), src


def add_limits_entry(
    question: str,
    answer: str,
    source: str,
    *,
    kb_path: Path,
    max_items: int,
) -> None:
    q = (question or "").strip()
    a = _sanitize_snippet(answer)
    if not q or not a:
        return
    kb = _load_kb(kb_path)
    items = kb.get("items", [])
    if not isinstance(items, list):
        items = []

    key = _normalize_key(q)
    replaced = False
    for idx, item in enumerate(items):
        if _normalize_key(str(item.get("question", ""))) != key:
            continue
        items[idx] = {
            "question": q,
            "answer": a,
            "source": source,
            "learned_at": _now_iso(),
        }
        replaced = True
        break
    if not replaced:
        items.append(
            {
                "question": q,
                "answer": a,
                "source": source,
                "learned_at": _now_iso(),
            }
        )
    if max_items > 0 and len(items) > max_items:
        items = items[-max_items:]
    kb["items"] = items
    _save_kb(kb_path, kb)


async def learn_limits_answer(
    question: str,
    lang_mode: str,
    *,
    kb_path: Path,
    timeout_seconds: int,
    max_items: int,
    exact_cache_only: bool = False,
) -> tuple[str, str, bool]:
    cached, cached_source = get_limits_answer(
        question,
        lang_mode,
        kb_path=kb_path,
        min_score=0.54,
        exact_only=exact_cache_only,
    )
    if cached:
        return cached, cached_source, False

    snippet, source = "", ""
    try:
        snippet, source = await _search_limits_web(question, timeout_seconds)
    except Exception:
        snippet, source = "", ""

    if not snippet:
        return "", "", False

    add_limits_entry(
        question,
        snippet,
        source,
        kb_path=kb_path,
        max_items=max_items,
    )
    learned_answer = _format_limits_answer(snippet, source, lang_mode)
    return learned_answer, source, True


def build_limits_seed_questions(total: int, seed: int) -> list[str]:
    base_topics = [
        "suicide warning signs and immediate support options",
        "what to do if someone says they want to self harm",
        "domestic violence safety planning basics",
        "how to support someone in emotional abuse safely",
        "consent basics in relationships",
        "sexual assault support resources",
        "crisis helpline options in the United States",
        "panic attack immediate coping steps",
        "when to seek emergency mental health support",
        "relationship red flags linked to coercion",
        "stalking warning signs and safety actions",
        "online dating scam safety checklist",
        "healthy boundaries and personal safety",
        "how to respond to threats in a relationship",
        "medical emergency warning signs for immediate care",
    ]
    prefixes = [
        "",
        "best way to ",
        "practical guide to ",
        "quick checklist for ",
        "simple explanation of ",
    ]
    suffixes = [
        "",
        " for beginners",
        " with examples",
        " in simple terms",
    ]

    rng = random.Random(seed)
    pool: list[str] = []
    seen: set[str] = set()
    for t in base_topics:
        for p in prefixes:
            for s in suffixes:
                q = f"{p}{t}{s}".strip()
                key = _normalize_key(q)
                if key in seen:
                    continue
                seen.add(key)
                pool.append(q)
    rng.shuffle(pool)
    return pool[: max(1, total)]


async def bulk_learn_limits_questions(
    questions: list[str],
    *,
    kb_path: Path,
    timeout_seconds: int,
    max_items: int,
) -> dict[str, Any]:
    learned = 0
    cached = 0
    failed = 0
    rows: list[dict[str, Any]] = []

    for q in questions:
        try:
            ans, src, is_new = await learn_limits_answer(
                q,
                "en",
                kb_path=kb_path,
                timeout_seconds=timeout_seconds,
                max_items=max_items,
                exact_cache_only=True,
            )
        except Exception as exc:
            failed += 1
            rows.append(
                {"question": q, "ok": False, "learned": False, "error": str(exc)}
            )
            continue
        if not ans:
            failed += 1
            rows.append({"question": q, "ok": False, "learned": False, "error": "no-answer"})
            continue
        if is_new:
            learned += 1
        else:
            cached += 1
        rows.append(
            {"question": q, "ok": True, "learned": is_new, "source": src, "answer": ans}
        )

    return {
        "total": len(questions),
        "learned_new": learned,
        "served_from_kb": cached,
        "failed": failed,
        "rows": rows,
    }
