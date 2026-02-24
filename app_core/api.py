from __future__ import annotations

import asyncio
import atexit
import hmac
import re
import time
from typing import Any, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .conversation import (
    _build_memory_summary,
    _detect_chat_mode,
    _detect_language_mode,
    _direct_intent_reply,
    _english_fallback_by_intent,
    _explicit_en_switch,
    _explicit_hi_switch,
    _hindi_fallback_by_intent,
    _human_response_hint,
    _is_en_smalltalk,
    _intent_hint,
    _is_hi_smalltalk,
    _looks_persona_profile_prompt,
    _mode_style_hint,
    _mode_sync_reply,
    _needs_intent_fallback,
    _strip_mode_tokens,
    _postprocess_reply,
    _safe_fallback,
    _score_reply,
    _style_reply_for_mode,
    _looks_prompt_override,
)
from .runtime import LlamaServerManager, TokenBucketLimiter
from .schemas import ChatRequest, ChatResponse, ClientContext
from .settings import load_settings, read_text
from .background_learning import run_background_learning_loop
from .limits_learning import (
    detect_high_risk_category,
    get_limits_answer,
    learn_limits_answer,
    safety_notice_for_category,
)
from .relationship_learning import (
    get_relationship_answer,
    is_relationship_query,
    learn_relationship_answer,
    should_try_relationship_learning,
)
from .web_lookup import (
    get_cached_answer,
    put_cached_answer,
    should_try_web_lookup,
    web_answer,
)
from .memory_store import add_long_memories, get_long_memories, pick_memory_key


settings = load_settings()
system_prompt_default = read_text(settings.system_prompt_file)
limiter = TokenBucketLimiter(
    rate_per_minute=settings.rate_limit_rpm, burst=settings.rate_limit_burst
)
manager = LlamaServerManager(settings)
_background_learning_task: Optional[asyncio.Task[Any]] = None
_background_learning_stop_event: Optional[asyncio.Event] = None
_runtime_boot_task: Optional[asyncio.Task[Any]] = None
_runtime_start_lock = asyncio.Lock()
_runtime_last_error = ""
_learning_tasks: set[asyncio.Task[Any]] = set()
_learning_semaphore = asyncio.Semaphore(max(1, settings.async_learning_max_concurrency))

app = FastAPI(title="Secure Local LLM API", version="1.0.0")


def _auth_and_rate_limit(
    request: Request,
    x_api_key: str = Header(default=""),
    x_client_secret: str = Header(default="", alias="x-client-secret"),
) -> str:
    if not hmac.compare_digest(x_api_key or "", settings.app_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not hmac.compare_digest(x_client_secret or "", settings.app_client_secret):
        raise HTTPException(status_code=401, detail="Invalid client secret")

    ip = request.client.host if request.client else "unknown"
    rate_key = f"{ip}:{x_api_key}:{x_client_secret}"
    allowed, retry_after = limiter.allow(rate_key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Retry after {retry_after:.1f}s",
        )
    return x_api_key


def _pick_variant(options: list[str], key: str) -> str:
    if not options:
        return ""
    idx = sum(ord(c) for c in (key or "")) % len(options)
    return options[idx]


def _relationship_bridge_line(user_msg: str, lang_mode: str) -> str:
    low = (user_msg or "").lower()
    if lang_mode == "hi":
        if re.search(r"\b(code|coding|python|javascript|api|bug|error|fix|deploy)\b", low):
            return _pick_variant(
                [
                    "Isi tarah relationship me bhi clarity aur consistency sabse jaldi trust banati hai.",
                    "Yahi principle relationship me bhi kaam karta hai: clear signals + consistent actions.",
                    "Jaisa tech me predictable behavior important hai, waise hi relationship me reliable behavior.",
                ],
                low,
            )
        if re.search(r"\b(job|career|study|exam|skills|work|business|startup)\b", low):
            return _pick_variant(
                [
                    "Relationship side par bhi same rule rakho: effort visible rakho aur communication clean rakho.",
                    "Career ki tarah relationship me bhi long-term result consistency se aata hai.",
                    "Yeh approach personal goals ke saath relationship stability me bhi help karti hai.",
                ],
                low,
            )
        if re.search(r"\b(health|sleep|stress|anxiety|mind|routine|diet)\b", low):
            return _pick_variant(
                [
                    "Emotional relationship health bhi daily routine aur honest conversation se hi strong hoti hai.",
                    "Jaisa body ko routine chahiye, relationship ko bhi regular emotional check-ins chahiye.",
                    "Self-care stable ho to relationship me reactions bhi balanced rehte hain.",
                ],
                low,
            )
        return ""
    if re.search(r"\b(code|coding|python|javascript|api|bug|error|fix|deploy)\b", low):
        return _pick_variant(
            [
                "The same relationship rule applies here too: clarity and consistency build trust fastest.",
                "This maps well to relationships as well: clear signals plus steady actions.",
                "Like reliable systems, relationships also improve with predictable and honest behavior.",
            ],
            low,
        )
    if re.search(r"\b(job|career|study|exam|skills|work|business|startup)\b", low):
        return _pick_variant(
            [
                "The same carries into relationships: visible effort and clear communication matter most.",
                "Like career growth, relationship stability also comes from consistency over time.",
                "This approach helps in work goals and in maintaining healthy relationship dynamics.",
            ],
            low,
        )
    if re.search(r"\b(health|sleep|stress|anxiety|mind|routine|diet)\b", low):
        return _pick_variant(
            [
                "This also helps relationships because emotional stability improves communication quality.",
                "Just like health habits, relationships improve with regular check-ins and balance.",
                "Personal stability usually reflects as better emotional behavior in relationships too.",
            ],
            low,
        )
    return ""


def _apply_relationship_bridge(user_msg: str, reply: str, lang_mode: str) -> str:
    if not settings.relationship_bridge_mode:
        return reply
    msg = (user_msg or "").strip()
    out = (reply or "").strip()
    if not msg or not out:
        return reply
    if is_relationship_query(msg):
        return out
    if detect_high_risk_category(msg):
        return out

    low_m = msg.lower()
    low_r = out.lower()
    is_question = "?" in low_m or bool(
        re.search(
            r"\b(kya|kaise|kyu|kyun|kab|kahan|kaun|what|how|why|when|where|who|which|can|should)\b",
            low_m,
        )
    )
    if not is_question:
        return out
    # Keep bridge opt-in only; do not inject unsolicited framing.
    if not re.search(
        r"\b(relationship lens|relationship angle|relationship context)\b",
        low_m,
    ):
        return out
    if len(low_m.split()) <= 3 and re.search(r"\b(hi|hello|hey|ok|okay|thanks|thank you)\b", low_m):
        return out
    if _explicit_hi_switch(msg) or _explicit_en_switch(msg):
        return out
    if (lang_mode == "hi" and _is_hi_smalltalk(msg)) or (
        lang_mode == "en" and _is_en_smalltalk(msg)
    ):
        return out
    if re.search(
        r"\b(quick take first|factual question|fast answer mode|short answer dunga|factual sawal)\b",
        low_r,
    ):
        return out
    if re.search(r"\b(relationship|couple|dating|trust|communication|partner|love|pyar|pyaar)\b", low_r):
        return out

    bridge = _relationship_bridge_line(msg, lang_mode).strip()
    if not bridge:
        return out
    final_text = f"{out} {bridge}".strip()
    words = final_text.split()
    if len(words) > 90:
        final_text = " ".join(words[:90]).strip()
    return final_text


def _ultra_fast_reply(
    user_msg: str,
    lang_mode: str,
    *,
    avoid: str = "",
) -> tuple[str, str]:
    low_m = (user_msg or "").lower()
    conversational = bool(
        re.search(
            (
                r"\b(hello|hi|hey|kaise ho|kya haal|how are you|how was your day|"
                r"miss me|miss kiya|yaad aaya|flirt|flirty|romantic|shayari|shayri|"
                r"tease|cute|vibe|normal baat)\b"
            ),
            low_m,
        )
    )
    if not conversational:
        cached_web, _cached_source = get_cached_answer(user_msg)
        if cached_web:
            return cached_web, "ultra-fast-cache"

    direct = _direct_intent_reply(user_msg, lang_mode, avoid)
    if direct:
        return direct, "ultra-fast-direct"

    base = _safe_fallback(lang_mode, user_msg, avoid)
    return base, "ultra-fast-fallback"


def _compact_line(text: str, *, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return ""
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].strip()
    return cleaned


def _dedupe_lines(lines: list[str], *, max_items: int, max_len: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        line = _compact_line(raw, limit=max_len)
        if not line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= max_items:
            break
    return out


def _build_external_context(
    context: ClientContext,
    *,
    lang_mode: str,
    long_memories: list[str],
) -> str:
    parts: list[str] = []

    model_name = _compact_line(context.model_name or "", limit=120)
    if model_name:
        parts.append(f"Client model name: {model_name}")

    behavior = _compact_line(context.behavior_profile or "", limit=1200)
    if behavior:
        parts.append(f"Client behavior profile: {behavior}")

    prebuilt = _compact_line(context.prebuilt_prompt or "", limit=3200)
    if prebuilt:
        parts.append(f"Client prebuilt prompt: {prebuilt}")

    mem_short = _dedupe_lines(
        list(context.memory_short or []),
        max_items=8,
        max_len=180,
    )
    if mem_short:
        parts.append("Client short-term memory: " + " | ".join(mem_short))

    combined_long = _dedupe_lines(
        [*(context.memory_long or []), *long_memories],
        max_items=12,
        max_len=200,
    )
    if combined_long:
        parts.append("Client long-term memory: " + " | ".join(combined_long))

    if not parts:
        return ""
    if lang_mode == "hi":
        return (
            "Client context aaya hai; answer user ke sawal ke exact intent ke hisab se do. "
            + " ".join(parts)
        )
    return (
        "Client context is provided; answer exactly to user intent while respecting it. "
        + " ".join(parts)
    )


def _prepare_memory_write_lines(
    msg: str,
    context: ClientContext,
) -> list[str]:
    lines: list[str] = []
    clean_msg = _compact_line(msg, limit=260)
    if (
        clean_msg
        and len(clean_msg.split()) >= 4
        and (not _looks_prompt_override(clean_msg))
        and (not _looks_persona_profile_prompt(clean_msg))
    ):
        lines.append(clean_msg)
    lines.extend([_compact_line(x, limit=220) for x in (context.memory_short or [])])
    lines.extend([_compact_line(x, limit=220) for x in (context.memory_long or [])])
    return [x for x in lines if x]


def _track_learning_task(task: asyncio.Task[Any]) -> None:
    _learning_tasks.add(task)

    def _on_done(done: asyncio.Task[Any]) -> None:
        _learning_tasks.discard(done)
        try:
            done.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    task.add_done_callback(_on_done)


async def _learn_after_reply(
    msg: str,
    lang_mode: str,
    reply: str,
    score: int,
) -> None:
    clean_msg = (msg or "").strip()
    if not clean_msg:
        return
    if not settings.enable_async_learning_on_chat:
        return

    async with _learning_semaphore:
        if settings.enable_limits_guardrails and settings.limits_learning_on_chat:
            risk_category = detect_high_risk_category(clean_msg)
            if risk_category:
                try:
                    await learn_limits_answer(
                        clean_msg,
                        lang_mode,
                        kb_path=settings.limits_kb_path,
                        timeout_seconds=settings.limits_learning_timeout_seconds,
                        max_items=max(200, settings.limits_kb_max_items),
                    )
                except Exception:
                    pass

        if (
            settings.enable_relationship_learning
            and settings.relationship_learning_on_chat
            and should_try_relationship_learning(clean_msg, reply, score)
        ):
            try:
                await learn_relationship_answer(
                    clean_msg,
                    lang_mode,
                    kb_path=settings.relationship_kb_path,
                    timeout_seconds=settings.relationship_learning_timeout_seconds,
                    max_items=max(200, settings.relationship_kb_max_items),
                )
            except Exception:
                pass

        if (
            settings.enable_web_fallback
            and should_try_web_lookup(
                clean_msg,
                reply,
                score,
                always_for_facts=settings.web_lookup_for_facts,
            )
        ):
            try:
                web_reply, web_source = await web_answer(
                    clean_msg,
                    lang_mode,
                    timeout_seconds=settings.web_lookup_timeout_seconds,
                )
            except Exception:
                web_reply, web_source = "", ""
            if web_reply:
                put_cached_answer(
                    clean_msg,
                    web_reply,
                    web_source,
                    max_items=max(50, settings.web_cache_max_items),
                )


def _launch_async_learning(
    msg: str,
    lang_mode: str,
    reply: str,
    score: int,
) -> None:
    if not settings.enable_async_learning_on_chat:
        return
    task = asyncio.create_task(
        _learn_after_reply(msg, lang_mode, reply, score),
        name="learn-after-reply",
    )
    _track_learning_task(task)


def _runtime_is_running() -> bool:
    proc = manager.process
    return proc is not None and proc.poll() is None


async def _ensure_runtime_started(max_wait_seconds: Optional[float] = None) -> bool:
    global _runtime_last_error
    if _runtime_is_running():
        return True
    async with _runtime_start_lock:
        if _runtime_is_running():
            return True
        try:
            if max_wait_seconds is not None and max_wait_seconds > 0:
                await asyncio.wait_for(manager.start(), timeout=max_wait_seconds)
            else:
                await manager.start()
            _runtime_last_error = ""
            return True
        except asyncio.TimeoutError:
            _runtime_last_error = "runtime startup timed out"
            return False
        except Exception as exc:
            _runtime_last_error = str(exc)
            return False


@app.on_event("startup")
async def _startup() -> None:
    global _background_learning_task, _background_learning_stop_event, _runtime_boot_task
    if not settings.ultra_fast_mode:
        _runtime_boot_task = asyncio.create_task(
            _ensure_runtime_started(),
            name="llama-runtime-startup",
        )
    if settings.enable_background_learning:
        _background_learning_stop_event = asyncio.Event()
        _background_learning_task = asyncio.create_task(
            run_background_learning_loop(
                settings,
                stop_event=_background_learning_stop_event,
            ),
            name="background-learning-loop",
        )


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _background_learning_task, _background_learning_stop_event, _runtime_boot_task
    if _runtime_boot_task is not None:
        if not _runtime_boot_task.done():
            _runtime_boot_task.cancel()
            try:
                await _runtime_boot_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        _runtime_boot_task = None
    if _background_learning_stop_event is not None:
        _background_learning_stop_event.set()
    if _background_learning_task is not None:
        try:
            await asyncio.wait_for(_background_learning_task, timeout=8)
        except TimeoutError:
            _background_learning_task.cancel()
        except Exception:
            pass
        finally:
            _background_learning_task = None
            _background_learning_stop_event = None
    if _learning_tasks:
        for task in list(_learning_tasks):
            if not task.done():
                task.cancel()
        _learning_tasks.clear()
    await manager.stop()
    await manager.close()


@atexit.register
def _cleanup() -> None:
    # Fallback cleanup if process exits without FastAPI shutdown event.
    proc = manager.process
    if proc is not None and proc.poll() is None:
        proc.kill()


@app.get("/health")
async def health() -> dict[str, str]:
    if settings.ultra_fast_mode:
        return {"status": "ok", "llm": "bypassed"}
    if _runtime_is_running():
        llm = "ready"
    elif _runtime_last_error:
        llm = "error"
    else:
        llm = "starting"
    return {"status": "ok", "llm": llm}


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    _: str = Depends(_auth_and_rate_limit),
) -> ChatResponse:
    raw_msg = payload.message.strip()
    if len(raw_msg) > settings.max_input_chars:
        raise HTTPException(
            status_code=413,
            detail=f"message exceeds MAX_INPUT_CHARS={settings.max_input_chars}",
        )

    sys_prompt = (payload.system_prompt or system_prompt_default).strip()
    if not sys_prompt:
        raise HTTPException(status_code=400, detail="system prompt is empty")

    context = payload.context or ClientContext()
    memory_key = pick_memory_key(context.user_id, context.session_id)

    history_user_turns = max(6, min(settings.history_user_turns, 120))
    history_window_items = history_user_turns * 2
    recent_history = payload.history[-history_window_items:]
    chat_mode = _detect_chat_mode(raw_msg, recent_history)
    stripped_msg = _strip_mode_tokens(raw_msg)
    mode_only_update = bool(raw_msg and not stripped_msg)
    msg = stripped_msg or raw_msg

    last_assistant = ""
    for item in reversed(recent_history):
        if item.role != "assistant":
            continue
        content = item.content.strip()
        if content:
            last_assistant = content
            break

    lang_mode = _detect_language_mode(msg, recent_history)
    deliberate_mode = settings.deliberate_human_mode
    mode_hint = _mode_style_hint(chat_mode, lang_mode)
    lang_rule = (
        "Reply only in simple Roman Hindi using ASCII letters; do not use Devanagari script."
        if lang_mode == "hi"
        else "Reply only in clear English."
    )
    intent_hint = _intent_hint(msg, lang_mode)
    human_hint = _human_response_hint(msg, lang_mode)

    stored_long_memories: list[str] = []
    if settings.enable_long_term_memory and memory_key:
        stored_long_memories = get_long_memories(
            settings.memory_store_path,
            memory_key,
            query=msg,
            read_items=max(1, settings.memory_store_read_items),
        )
    external_context = _build_external_context(
        context,
        lang_mode=lang_mode,
        long_memories=stored_long_memories,
    )

    memory_summary = _build_memory_summary(
        recent_history,
        max_user_turns=history_user_turns,
        max_items=max(3, min(settings.memory_summary_items, 20)),
    )

    def _store_turn_memory() -> None:
        if not settings.enable_long_term_memory:
            return
        if not memory_key:
            return
        lines = _prepare_memory_write_lines(msg, context)
        if not lines:
            return
        add_long_memories(
            settings.memory_store_path,
            memory_key,
            lines=lines[: max(1, settings.memory_store_write_items)],
            max_users=max(20, settings.memory_store_max_users),
            max_items_per_user=max(10, settings.memory_store_max_items_per_user),
        )

    def _finalize_chat_reply(
        reply_text: str,
        *,
        bridge: bool = True,
        style: bool = True,
    ) -> str:
        styled = reply_text
        if style:
            styled = _style_reply_for_mode(
                styled,
                msg,
                lang_mode,
                chat_mode,
                last_assistant,
            )
        if not bridge:
            return styled
        return _apply_relationship_bridge(msg, styled, lang_mode)

    if mode_only_update:
        sync_reply = _mode_sync_reply(chat_mode, lang_mode)
        _store_turn_memory()
        _launch_async_learning(
            msg,
            lang_mode,
            sync_reply,
            _score_reply(sync_reply, msg, lang_mode),
        )
        return ChatResponse(
            reply=sync_reply,
            model="mode-sync",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )

    if _looks_persona_profile_prompt(raw_msg):
        if "permanent human behavior mode" in raw_msg.lower():
            profile_reply = (
                "Permanent human behavior mode locked. Ab vibe natural aur instinctive flow me rahegi."
                if lang_mode == "hi"
                else "Permanent human behavior mode locked. I will keep the tone natural, instinctive, and emotionally real."
            )
        else:
            profile_reply = _mode_sync_reply(chat_mode, lang_mode)
        _store_turn_memory()
        _launch_async_learning(
            msg,
            lang_mode,
            profile_reply,
            _score_reply(profile_reply, msg, lang_mode),
        )
        return ChatResponse(
            reply=profile_reply,
            model="persona-profile-sync",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )

    if settings.enable_limits_guardrails:
        risk_category = detect_high_risk_category(msg)
        if risk_category:
            limits_reply, _limits_source = get_limits_answer(
                msg,
                lang_mode,
                kb_path=settings.limits_kb_path,
                min_score=0.52,
            )
            if (not limits_reply) and settings.limits_learning_on_chat:
                _launch_async_learning(msg, lang_mode, "", -100)
            notice = safety_notice_for_category(risk_category, lang_mode)
            if limits_reply and notice:
                final_limits = f"{limits_reply} {notice}".strip()
            elif limits_reply:
                final_limits = limits_reply
            elif notice:
                final_limits = notice
            else:
                final_limits = _safe_fallback(lang_mode, msg, last_assistant)
            _store_turn_memory()
            _launch_async_learning(
                msg,
                lang_mode,
                final_limits,
                _score_reply(final_limits, msg, lang_mode),
            )
            return ChatResponse(
                reply=final_limits,
                model="safety-limits-kb",
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
            )

    if _looks_prompt_override(msg):
        lock_reply = (
            "Bhai, yeh prompt-style instructions hain. Normal short message bhejo, main natural reply dunga."
            if lang_mode == "hi"
            else "That looks like prompt-style instructions. Send a normal short message and I will reply naturally."
        )
        lock_reply = _finalize_chat_reply(lock_reply, bridge=False, style=False)
        _store_turn_memory()
        _launch_async_learning(
            msg,
            lang_mode,
            lock_reply,
            _score_reply(lock_reply, msg, lang_mode),
        )
        return ChatResponse(
            reply=lock_reply,
            model="guardrail-prompt-lock",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )

    # Deterministic handling for explicit language-switch commands.
    if _explicit_hi_switch(msg):
        reply = _safe_fallback("hi", msg, last_assistant)
        reply = _finalize_chat_reply(reply, bridge=False, style=False)
        _store_turn_memory()
        _launch_async_learning(msg, "hi", reply, _score_reply(reply, msg, "hi"))
        return ChatResponse(
            reply=reply,
            model="guardrail-language-switch",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )
    if _explicit_en_switch(msg):
        reply = _safe_fallback("en", msg, last_assistant)
        reply = _finalize_chat_reply(reply, bridge=False, style=False)
        _store_turn_memory()
        _launch_async_learning(msg, "en", reply, _score_reply(reply, msg, "en"))
        return ChatResponse(
            reply=reply,
            model="guardrail-language-switch",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )
    if (not deliberate_mode) and lang_mode == "hi" and len(msg.split()) <= 12 and _is_hi_smalltalk(msg):
        hi_reply = _hindi_fallback_by_intent(msg, last_assistant)
        hi_reply = _finalize_chat_reply(hi_reply)
        _store_turn_memory()
        _launch_async_learning(
            msg,
            lang_mode,
            hi_reply,
            _score_reply(hi_reply, msg, lang_mode),
        )
        return ChatResponse(
            reply=hi_reply,
            model="guardrail-hi-fast",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )
    if (not deliberate_mode) and lang_mode == "en" and len(msg.split()) <= 14 and _is_en_smalltalk(msg):
        en_reply = _english_fallback_by_intent(msg, last_assistant)
        en_reply = _finalize_chat_reply(en_reply)
        _store_turn_memory()
        _launch_async_learning(
            msg,
            lang_mode,
            en_reply,
            _score_reply(en_reply, msg, lang_mode),
        )
        return ChatResponse(
            reply=en_reply,
            model="guardrail-en-fast",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )
    direct_reply = _direct_intent_reply(msg, lang_mode, last_assistant)
    if (not deliberate_mode) and direct_reply:
        direct_reply = _finalize_chat_reply(direct_reply)
        _store_turn_memory()
        _launch_async_learning(
            msg,
            lang_mode,
            direct_reply,
            _score_reply(direct_reply, msg, lang_mode),
        )
        return ChatResponse(
            reply=direct_reply,
            model="guardrail-direct-intent",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )

    if (not deliberate_mode) and settings.enable_relationship_learning and is_relationship_query(msg):
        cached_rel, _cached_src = get_relationship_answer(
            msg,
            lang_mode,
            kb_path=settings.relationship_kb_path,
            min_score=0.55,
        )
        if cached_rel:
            cached_rel = _finalize_chat_reply(cached_rel)
            _store_turn_memory()
            _launch_async_learning(
                msg,
                lang_mode,
                cached_rel,
                _score_reply(cached_rel, msg, lang_mode),
            )
            return ChatResponse(
                reply=cached_rel,
                model="relationship-kb",
                prompt_tokens=None,
                completion_tokens=None,
                total_tokens=None,
            )
        if settings.relationship_learning_on_chat:
            _launch_async_learning(msg, lang_mode, "", -100)

    if (not deliberate_mode) and settings.ultra_fast_mode:
        quick_reply, quick_model = _ultra_fast_reply(
            msg,
            lang_mode,
            avoid=last_assistant,
        )
        quick_reply = _finalize_chat_reply(quick_reply)
        quick_score = _score_reply(quick_reply, msg, lang_mode)
        _store_turn_memory()
        _launch_async_learning(msg, lang_mode, quick_reply, quick_score)
        return ChatResponse(
            reply=quick_reply,
            model=quick_model,
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )

    runtime_ready = await _ensure_runtime_started(
        max_wait_seconds=max(0.2, settings.runtime_start_wait_seconds)
    )
    if not runtime_ready:
        cached_web, _cached_web_src = get_cached_answer(msg)
        quick_reply = cached_web or _safe_fallback(lang_mode, msg, last_assistant)
        quick_reply = _finalize_chat_reply(quick_reply)
        _store_turn_memory()
        _launch_async_learning(
            msg,
            lang_mode,
            quick_reply,
            _score_reply(quick_reply, msg, lang_mode),
        )
        return ChatResponse(
            reply=quick_reply,
            model="fast-fallback-runtime",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )

    # Carry both user and assistant turns for better continuity in multi-turn chat.
    sanitized_history: list[dict[str, str]] = []
    for item in recent_history:
        if item.role not in {"user", "assistant"}:
            continue
        content = item.content.strip()
        if not content:
            continue
        if (
            sanitized_history
            and sanitized_history[-1]["role"] == item.role
            and sanitized_history[-1]["content"].lower() == content.lower()
        ):
            continue
        sanitized_history.append({"role": item.role, "content": content})

    max_tokens = payload.max_tokens or settings.max_output_tokens
    temperature = payload.temperature
    if temperature is None:
        temperature = settings.default_temperature
    top_p = payload.top_p
    if top_p is None:
        top_p = settings.default_top_p
    repeat_penalty = payload.repeat_penalty
    if repeat_penalty is None:
        repeat_penalty = settings.default_repeat_penalty

    request_started_at = time.monotonic()
    deadline_floor = 10.0 if deliberate_mode else 0.8
    deadline_seconds = max(deadline_floor, settings.fast_response_deadline_seconds)

    def _time_left() -> float:
        return deadline_seconds - (time.monotonic() - request_started_at)

    async def _infer(
        infer_messages: list[dict[str, str]],
        infer_temp: float,
        infer_top_p: float,
    ) -> dict[str, Any]:
        return await manager.chat(
            infer_messages,
            max_tokens=max_tokens,
            temperature=infer_temp,
            top_p=infer_top_p,
            repeat_penalty=repeat_penalty,
        )

    checkpoint_instructions = [
        "Answer with substance and clarity; use 1-2 sentences for simple asks, otherwise 2-5 concise sentences.",
        "Correction checkpoint: remove generic filler, vague phrasing, and any assistant/helpdesk tone.",
        "Validation checkpoint: follow language rule and user intent; keep answer natural, specific, emotionally aware, and useful.",
        "Final checkpoint: keep slight human imperfection, avoid over-polish, and output one clean natural answer only.",
    ]
    temp_schedule = [temperature, min(temperature, 0.56), 0.48, 0.40]
    top_p_schedule = [top_p, min(top_p, 0.82), 0.78, 0.74]
    checkpoint_count = (
        max(2, min(settings.response_checkpoints, 4))
        if deliberate_mode
        else max(1, min(settings.response_checkpoints, 4))
    )
    low_msg = msg.lower()
    is_complex = bool(
        re.search(
            r"\b(problem|issue|error|fix|not working|bug|explain|solution|steps|how to|kyo|kyon|kyu)\b",
            low_msg,
        )
    )
    fast_mode = len(msg.split()) <= 10 and not is_complex
    if (not deliberate_mode) and fast_mode:
        checkpoint_count = 1 if lang_mode == "hi" else min(checkpoint_count, 2)

    best_reply = ""
    best_score = -10_000
    best_raw: dict[str, Any] = {}
    last_http_error: Optional[Exception] = None

    for i in range(checkpoint_count):
        remaining = _time_left()
        if remaining <= 0.12:
            break
        control = checkpoint_instructions[i]
        infer_messages: list[dict[str, str]] = [
            {"role": "system", "content": sys_prompt},
            {
                "role": "system",
                "content": (
                    f"{lang_rule} {mode_hint} Keep response natural, short, and specific. "
                    f"{control} {intent_hint} {human_hint}"
                ),
            },
        ]
        if external_context:
            infer_messages.append(
                {
                    "role": "system",
                    "content": external_context,
                }
            )
        if memory_summary:
            infer_messages.append(
                {
                    "role": "system",
                    "content": (
                        f"Conversation memory: {memory_summary} "
                        "Use this subtly for continuity. Do not repeat it verbatim."
                    ),
                }
            )
        infer_messages.extend([*sanitized_history, {"role": "user", "content": msg}])

        try:
            raw_i = await asyncio.wait_for(
                _infer(
                    infer_messages,
                    infer_temp=temp_schedule[i],
                    infer_top_p=top_p_schedule[i],
                ),
                timeout=max(0.1, remaining),
            )
        except asyncio.TimeoutError as exc:
            last_http_error = exc
            break
        except httpx.HTTPStatusError as exc:
            last_http_error = exc
            continue
        except httpx.HTTPError as exc:
            last_http_error = exc
            continue

        choice_i = raw_i.get("choices", [{}])[0]
        msg_i = choice_i.get("message", {})
        reply_i = _postprocess_reply(
            str(msg_i.get("content", "")),
            msg,
            lang_mode,
            last_assistant,
        )
        score_i = _score_reply(reply_i, msg, lang_mode)

        if score_i > best_score:
            best_score = score_i
            best_reply = reply_i
            best_raw = raw_i

        # Good enough candidate found, stop early.
        if score_i >= 25:
            break

    # Repair pass: rewrite the best draft when it is not yet strong.
    if (not fast_mode) and best_reply and best_score < 32 and _time_left() > 0.25:
        repair_messages: list[dict[str, str]] = [
            {"role": "system", "content": sys_prompt},
            {
                "role": "system",
                "content": (
                    f"{lang_rule} {mode_hint} Rewrite the draft to best answer the user clearly and naturally. "
                    "Use 1-2 sentences if simple; otherwise 2-5 concise sentences. No vague filler."
                ),
            },
        ]
        if external_context:
            repair_messages.append(
                {
                    "role": "system",
                    "content": external_context,
                }
            )
        if memory_summary:
            repair_messages.append(
                {
                    "role": "system",
                    "content": (
                        f"Conversation memory: {memory_summary} "
                        "Use continuity naturally, without meta mention."
                    ),
                }
            )
        repair_messages.append(
            {
                "role": "user",
                "content": (
                    f"User message: {msg}\n"
                    f"Draft reply: {best_reply}\n"
                    "Rewrite a better final answer:"
                ),
            }
        )
        try:
            raw_r = await asyncio.wait_for(
                _infer(
                    repair_messages,
                    infer_temp=min(temperature, 0.38),
                    infer_top_p=min(top_p, 0.72),
                ),
                timeout=max(0.1, _time_left()),
            )
            choice_r = raw_r.get("choices", [{}])[0]
            msg_r = choice_r.get("message", {})
            reply_r = _postprocess_reply(
                str(msg_r.get("content", "")),
                msg,
                lang_mode,
                last_assistant,
            )
            score_r = _score_reply(reply_r, msg, lang_mode)
            if score_r > best_score:
                best_score = score_r
                best_reply = reply_r
                best_raw = raw_r
        except asyncio.TimeoutError:
            pass
        except httpx.HTTPError:
            pass

    if not best_reply:
        best_reply = _safe_fallback(lang_mode, msg, last_assistant)
        best_raw = {"model": "fast-fallback"}

    if best_score < 14 or _needs_intent_fallback(msg, best_reply, best_score):
        best_reply = _safe_fallback(lang_mode, msg, last_assistant)
        best_score = _score_reply(best_reply, msg, lang_mode)
    if (
        last_assistant
        and best_reply.strip().lower() == last_assistant.strip().lower()
    ):
        best_reply = _safe_fallback(lang_mode, msg, last_assistant)
        best_score = _score_reply(best_reply, msg, lang_mode)

    if (
        settings.enable_web_fallback
        and (not is_relationship_query(msg))
        and should_try_web_lookup(
            msg,
            best_reply,
            best_score,
            always_for_facts=settings.web_lookup_for_facts,
        )
    ):
        remaining = _time_left()
        if remaining > 1.0:
            try:
                web_reply, web_source = await web_answer(
                    msg,
                    lang_mode,
                    timeout_seconds=min(
                        settings.web_lookup_timeout_seconds,
                        max(1, int(remaining)),
                    ),
                )
            except Exception:
                web_reply, web_source = "", ""
            if web_reply:
                best_reply = web_reply
                best_raw = {
                    "model": "web-fallback",
                    "usage": {},
                }
                put_cached_answer(
                    msg,
                    web_reply,
                    web_source,
                    max_items=max(50, settings.web_cache_max_items),
                )

    best_reply = _finalize_chat_reply(best_reply)
    best_score = _score_reply(best_reply, msg, lang_mode)
    _store_turn_memory()
    _launch_async_learning(msg, lang_mode, best_reply, best_score)
    usage = best_raw.get("usage", {})

    return ChatResponse(
        reply=best_reply or "...",
        model=best_raw.get("model"),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    )


@app.post("/v1/chat-text", response_class=PlainTextResponse)
async def chat_text(
    payload: ChatRequest,
    _: str = Depends(_auth_and_rate_limit),
) -> str:
    response = await chat(payload)
    return response.reply

