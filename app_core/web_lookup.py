from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx


_CACHE_PATH = Path("knowledge_cache.json")
_CACHE: dict[str, dict[str, str]] | None = None
_DEFAULT_HEADERS = {
    "User-Agent": "local-persona-ai/1.0 (+https://localhost)",
    "Accept-Language": "en-US,en;q=0.8",
}
_TRUSTED_SOURCE_DOMAINS = {
    "en.wikipedia.org",
    "wikipedia.org",
    "britannica.com",
    "who.int",
    "un.org",
    "nasa.gov",
    "noaa.gov",
    "cdc.gov",
    "nih.gov",
    "mayoclinic.org",
    "worldbank.org",
    "imf.org",
    "oecd.org",
    "investopedia.com",
    "reuters.com",
    "apnews.com",
}
_BAD_SOURCE_DOMAINS = {
    "youtube.com",
    "m.youtube.com",
    "youtu.be",
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "pinterest.com",
    "ilyricshub.com",
}
_RELEVANCE_STOPWORDS = {
    "what",
    "who",
    "when",
    "where",
    "why",
    "how",
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "your",
    "about",
    "latest",
    "today",
    "current",
    "internet",
    "online",
    "se",
    "batao",
    "wikipedia",
}


def _normalize_key(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip().lower())
    cleaned = re.sub(r"[^a-z0-9 ?!.,-]", "", cleaned)
    return cleaned[:300]


def _extract_domain(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().replace("www.", "")
    except Exception:
        return ""


def _is_bad_source(url: str) -> bool:
    domain = _extract_domain(url)
    if not domain:
        return True
    return domain in _BAD_SOURCE_DOMAINS


def _is_trusted_source(url: str) -> bool:
    domain = _extract_domain(url)
    if not domain:
        return False
    if domain in _TRUSTED_SOURCE_DOMAINS:
        return True
    return domain.endswith(".gov") or domain.endswith(".edu")


def _source_priority(url: str) -> int:
    return 0 if _is_trusted_source(url) else 1


def _identity_intent_bonus(question: str, source_url: str, text: str) -> int:
    low_q = (question or "").lower()
    if not re.search(
        r"\b(who|founder|ceo|president|prime minister|invented|discovered)\b",
        low_q,
    ):
        return 0
    bonus = 0
    if re.search(r"/wiki/[A-Za-z]+_[A-Za-z]+$", source_url):
        bonus += 3
    low_t = (text or "").lower()
    if re.search(r"\b(founder|ceo|president|invented|discovered|by)\b", low_t):
        bonus += 1
    return bonus


def _keyword_tokens(text: str) -> set[str]:
    raw = set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))
    return {t for t in raw if t not in _RELEVANCE_STOPWORDS}


def _is_candidate_relevant(question: str, candidate_text: str) -> bool:
    q_toks = _keyword_tokens(question)
    if not q_toks:
        return True
    c_toks = _keyword_tokens(candidate_text)
    if not c_toks:
        return False
    overlap = len(q_toks & c_toks)
    if overlap <= 0:
        return False
    coverage = overlap / float(len(q_toks))
    return coverage >= 0.20 or overlap >= 2


def _load_cache() -> dict[str, dict[str, str]]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    if not _CACHE_PATH.exists():
        _CACHE = {}
        return _CACHE
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    _CACHE = data
    return _CACHE


def _save_cache(cache: dict[str, dict[str, str]]) -> None:
    try:
        _CACHE_PATH.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        # Best-effort cache write; never fail request on cache I/O.
        pass


def _looks_mojibake(text: str) -> bool:
    if not text:
        return False
    return any(
        token in text
        for token in (
            "\ufffd",  # replacement char
            "\u00c3",  # common mojibake marker: Ã
            "\u00c2",  # common mojibake marker: Â
            "\u00e2",  # common mojibake marker: â
        )
    )


def get_cached_answer(question: str) -> tuple[str, str]:
    key = _normalize_key(question)
    if not key:
        return "", ""
    cache = _load_cache()
    row = cache.get(key)
    if not row:
        return "", ""
    answer = str(row.get("answer", "")).strip()
    source = str(row.get("source", "")).strip()
    if _looks_mojibake(answer) or _is_bad_source(source):
        cache.pop(key, None)
        _save_cache(cache)
        return "", ""
    return answer, source


def put_cached_answer(question: str, answer: str, source: str, *, max_items: int) -> None:
    key = _normalize_key(question)
    if not key or not answer:
        return
    cache = _load_cache()
    cache[key] = {"answer": answer, "source": source}
    if max_items > 0 and len(cache) > max_items:
        # Drop oldest entries by insertion order.
        while len(cache) > max_items:
            first_key = next(iter(cache))
            cache.pop(first_key, None)
    _save_cache(cache)


def should_try_web_lookup(
    user_msg: str,
    reply: str,
    score: int,
    *,
    always_for_facts: bool = False,
) -> bool:
    low_m = (user_msg or "").lower()
    low_r = (reply or "").lower()
    if not low_m.strip():
        return False

    conversational_hit = bool(
        re.search(
            (
                r"\b(hello|hi|hey|kaise ho|kese ho|kya haal|haal chal|"
                r"how are you|how was your day|you there|"
                r"miss me|yaad aaya|miss kiya|flirt|flirty|romantic|shayari|shayri|"
                r"tease|playful|mood|vibe|normal baat)\b"
            ),
            low_m,
        )
    )

    asks_internet = bool(
        re.search(
            r"\b(internet|web|online|latest|news|current|aaj ka|today|abhi ka|source)\b",
            low_m,
        )
    )
    asks_fact = bool(
        re.search(
            (
                r"\b(kisne|kab|kahan|kaun|kyu|kyun|kaise|what|who|when|where|why|"
                r"which|invented|invention|founder|price|rate|result|winner)\b"
            ),
            low_m,
        )
    )
    is_question = "?" in low_m or asks_fact

    generic_reply = bool(
        re.search(
            (
                r"\b(seedha pucho|exact point batao|topic do|i am listening|let me know|"
                r"short aur direct jawab dunga|drop one clear question|specific prompt)\b"
            ),
            low_r,
        )
    )
    unknown_reply = bool(
        re.search(
            r"\b(i don't know|not sure|pata nahi|malum nahi|unsure)\b",
            low_r,
        )
    )

    if asks_internet:
        return True
    if conversational_hit:
        return False
    if asks_fact and is_question and always_for_facts:
        return True
    if is_question and (score < 20 or generic_reply or unknown_reply):
        return True
    return False


def _shorten(text: str, limit: int = 320) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if len(compact) <= limit:
        return compact
    cut = compact[:limit]
    if "." in cut:
        cut = cut.rsplit(".", 1)[0] + "."
    return cut


def _clean_html_fragment(text: str) -> str:
    if not text:
        return ""
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = html.unescape(plain)
    return re.sub(r"\s+", " ", plain).strip()


def _unwrap_ddg_link(href: str) -> str:
    raw = html.unescape((href or "").strip())
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    parsed = urlparse(raw)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg_list = parse_qs(parsed.query).get("uddg", [])
        if uddg_list:
            return unquote(uddg_list[0]).strip()
    return raw


def _format_web_answer(snippet: str, source: str, lang_mode: str) -> str:
    clean = _shorten(snippet, 320)
    if lang_mode == "hi":
        return f"Internet source ke hisab se: {clean} Source: {source}"
    return f"From web sources: {clean} Source: {source}"


async def _duckduckgo_lookup(client: httpx.AsyncClient, question: str) -> tuple[str, str]:
    resp = await client.get(
        "https://api.duckduckgo.com/",
        params={
            "q": question,
            "format": "json",
            "no_html": "1",
            "no_redirect": "1",
            "skip_disambig": "1",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    abstract = str(data.get("AbstractText", "")).strip()
    abstract_url = str(data.get("AbstractURL", "")).strip()
    if (
        abstract
        and abstract_url
        and not _is_bad_source(abstract_url)
        and _is_candidate_relevant(question, abstract)
    ):
        return abstract, abstract_url

    related = data.get("RelatedTopics", [])
    if isinstance(related, list):
        for item in related:
            if not isinstance(item, dict):
                continue
            text = str(item.get("Text", "")).strip()
            url = str(item.get("FirstURL", "")).strip()
            if (
                text
                and url
                and not _is_bad_source(url)
                and _is_candidate_relevant(question, text)
            ):
                return text, url
            nested = item.get("Topics")
            if isinstance(nested, list):
                for sub in nested:
                    if not isinstance(sub, dict):
                        continue
                    text = str(sub.get("Text", "")).strip()
                    url = str(sub.get("FirstURL", "")).strip()
                    if (
                        text
                        and url
                        and not _is_bad_source(url)
                        and _is_candidate_relevant(question, text)
                    ):
                        return text, url
    return "", ""


async def _duckduckgo_html_lookup(
    client: httpx.AsyncClient, question: str
) -> tuple[str, str]:
    resp = await client.get(
        "https://duckduckgo.com/html/",
        params={"q": question},
    )
    resp.raise_for_status()
    page = resp.text

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
    candidates: list[tuple[int, int, str, str]] = []
    for idx, row in enumerate(links):
        href_raw, title_raw = row
        source = _unwrap_ddg_link(href_raw)
        if not source or _is_bad_source(source):
            continue
        title = _clean_html_fragment(title_raw)
        snippet = _clean_html_fragment(snippets[idx]) if idx < len(snippets) else ""
        if _looks_mojibake(snippet):
            snippet = ""
        if _looks_mojibake(title):
            title = ""
        if not snippet:
            snippet = title
        if not snippet:
            continue
        text = f"{title}: {snippet}" if title and title.lower() not in snippet.lower() else snippet
        if not _is_candidate_relevant(question, f"{title} {snippet}"):
            continue
        bonus = _identity_intent_bonus(question, source, text)
        candidates.append((_source_priority(source), -bonus, text, source))

    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]))
        _, _, best_text, best_source = candidates[0]
        return best_text, best_source
    return "", ""


async def _wikipedia_lookup(client: httpx.AsyncClient, question: str) -> tuple[str, str]:
    search = await client.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": question,
            "srlimit": "1",
            "format": "json",
            "utf8": "1",
        },
    )
    search.raise_for_status()
    data = search.json()
    hits = data.get("query", {}).get("search", [])
    if not isinstance(hits, list) or not hits:
        return "", ""

    title = str(hits[0].get("title", "")).strip()
    if not title:
        return "", ""
    encoded = quote(title, safe="")
    summary = await client.get(
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}"
    )
    summary.raise_for_status()
    row = summary.json()
    extract = str(row.get("extract", "")).strip()
    source = str(
        row.get("content_urls", {}).get("desktop", {}).get("page", "")
    ).strip()
    if not source:
        source = f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
    if extract:
        return extract, source
    return "", ""


async def web_answer(
    question: str,
    lang_mode: str,
    *,
    timeout_seconds: int,
) -> tuple[str, str]:
    low_q = (question or "").lower()
    asks_fresh = bool(
        re.search(r"\b(today|latest|current|abhi|aaj|news|price|rate|result|score)\b", low_q)
    )
    asks_fact = bool(
        re.search(
            (
                r"\b(kisne|kab|kahan|kaun|kyu|kyun|kaise|what|who|when|where|why|"
                r"which|invented|invention|founder|price|rate|result|winner)\b"
            ),
            low_q,
        )
    )
    asks_identity_fact = bool(
        re.search(
            r"\b(who|founder|ceo|president|prime minister|discovered|invented)\b",
            low_q,
        )
    )
    force_refresh = asks_fresh or asks_identity_fact

    if not force_refresh:
        cached_answer, cached_source = get_cached_answer(question)
        if cached_answer:
            return cached_answer, cached_source

    async with httpx.AsyncClient(
        timeout=max(4, timeout_seconds),
        headers=_DEFAULT_HEADERS,
        follow_redirects=True,
    ) as client:
        snippet, source = "", ""
        if asks_fact:
            if asks_identity_fact:
                try:
                    snippet, source = await _duckduckgo_html_lookup(
                        client, f"{question} wikipedia"
                    )
                except Exception:
                    snippet, source = "", ""
            try:
                if not snippet:
                    snippet, source = await _wikipedia_lookup(client, question)
            except Exception:
                snippet, source = "", ""
        if not snippet and asks_fresh:
            try:
                snippet, source = await _duckduckgo_html_lookup(client, question)
            except Exception:
                snippet, source = "", ""
        if not snippet:
            try:
                snippet, source = await _duckduckgo_lookup(client, question)
            except Exception:
                snippet, source = "", ""
        if not snippet:
            try:
                snippet, source = await _duckduckgo_html_lookup(client, question)
            except Exception:
                snippet, source = "", ""

    if not snippet or _is_bad_source(source):
        return "", ""
    answer = _format_web_answer(snippet, source, lang_mode)
    return answer, source

