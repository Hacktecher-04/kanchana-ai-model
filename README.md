# Secure Local LLM API (llama.cpp + GGUF)

This app runs a local `llama-server` process and exposes a secured FastAPI wrapper.
It uses persona prompt rules from `prompts/system_prompt.txt` and adds quality checks
before returning final replies.

## What this app provides

- `GET /health` readiness endpoint
- `POST /v1/chat` JSON response endpoint
- `POST /v1/chat-text` plain text endpoint
- Dual-secret auth (`x-api-key` + `x-client-secret`) + rate limiting
- Conversation guardrails: language control, anti-repeat fallback, intent checks

## Code architecture

- `api_service.py`: compatibility entrypoint used by Uvicorn/Render
- `app_core/api.py`: FastAPI routes and chat orchestration pipeline
- `app_core/conversation.py`: language detection, fallback logic, reply scoring
- `app_core/runtime.py`: `llama-server` process lifecycle + internal HTTP client + limiter
- `app_core/settings.py`: `.env` config loading and file helpers
- `app_core/web_lookup.py`: internet fallback + local knowledge cache
- `app_core/relationship_learning.py`: relationship/couple/friend topic auto-learning KB
- `app_core/limits_learning.py`: human-safety/limits KB + high-risk guardrail responses
- `app_core/background_learning.py`: continuous background learning loop
- `app_core/memory_store.py`: persistent long-term memory store keyed by user/session
- `app_core/schemas.py`: Pydantic request/response models

## Request flow

1. Client calls `/v1/chat` or `/v1/chat-text` with `x-api-key` and `x-client-secret`.
2. API validates key and enforces rate limit.
3. Message + history are normalized and language mode is selected.
4. System prompt + memory summary + intent hints are composed.
5. Model checkpoints run via local `llama-server`.
6. Best candidate is scored; optional repair pass improves weak drafts.
7. If reply is low-quality/wrong-language/repetitive, safe fallback is used.
8. Final reply is returned.

## Project structure

```text
.
|-- api_service.py
|-- app_core/
|   |-- __init__.py
|   |-- api.py
|   |-- background_learning.py
|   |-- conversation.py
|   |-- limits_learning.py
|   |-- memory_store.py
|   |-- runtime.py
|   |-- schemas.py
|   |-- settings.py
|   |-- relationship_learning.py
|   `-- web_lookup.py
|-- prompts/
|-- scripts/
`-- models/
```

## Quick start (Windows PowerShell)

### 1) Create venv

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-api.txt
```

### 2) Prepare runtime (optional on fresh machine)

```powershell
python scripts/bootstrap_runtime.py
```

### 3) Configure `.env`

```powershell
copy .env.example .env
```

Minimum required values:

- `APP_API_KEY`
- `APP_CLIENT_SECRET`
- `LLAMA_INTERNAL_API_KEY`
- `LLAMA_SERVER_PATH`
- `LLAMA_MODEL_PATH` (or `LLAMA_HF_REPO` + `LLAMA_HF_FILE`)

### 4) Run API

```powershell
python -m uvicorn api_service:app --host 0.0.0.0 --port 8000 --env-file .env
```

### 5) Quick API test

```powershell
curl.exe -s "http://127.0.0.1:8000/v1/chat-text" `
  -H "x-api-key: your-strong-app-key" `
  -H "x-client-secret: your-strong-client-secret" `
  -H "content-type: application/json" `
  -d "{\"message\":\"You seem interesting.\"}"
```

## Conversation testing

### Interactive 20-turn test

```powershell
.\scripts\run_20_chat.ps1 -Turns 20
```

Long-memory version:

```powershell
.\scripts\run_20_chat.ps1 -Turns 20 -MaxHistory 120
```

### Automated quality checks

```powershell
python scripts/run_api_checks.py --api-url http://127.0.0.1:8000 --api-key your-strong-app-key --client-secret your-strong-client-secret
```

This reports duplication/similarity and overall response diversity.

### 50 random-question demo (auto)

Run 50 mixed random prompts and save transcript + JSON summary:

```powershell
.\scripts\run_random_demo.ps1 -Count 50 -MaxHistory 16
```

Direct Python mode (no external API process required):

```powershell
python scripts/run_random_demo.py --count 50 --max-history 16 --direct
```

Output files:
- `transcripts/random_demo_50_YYYYMMDD_HHMMSS.txt`
- `transcripts/random_demo_50_YYYYMMDD_HHMMSS.json`

### Relationship knowledge auto-learning

This system can automatically learn relationship/couple/friend/human-thought topics from internet sources and store them in a local KB (`knowledge_relationship.json`).

Chat behavior:
- If a relationship query is already in KB, reply is served from KB.
- If not in KB and `RELATIONSHIP_LEARNING_ON_CHAT=1`, it fetches from web, stores it, and replies.
- Next similar query is answered from local KB (faster + consistent).
- If `RELATIONSHIP_BRIDGE_MODE=1`, non-relationship questions are answered first, then a short relationship-context bridge line is added.
- Informational questions are routed to normal answer pipeline first (model/web), to avoid generic canned replies.

Batch learn command:

```powershell
python scripts/learn_relationship_kb.py --count 120 --timeout 14
```

For continuous learning mode, keep `RELATIONSHIP_LEARNING_ON_CHAT=1`.
This project now runs learn-on-chat asynchronously, so replies are not blocked by learning calls.

Optional continuous learner loop (for background worker / Render worker):

```powershell
python scripts/relationship_learning_loop.py --count-per-cycle 40 --sleep-minutes 180
```

This keeps refreshing the relationship KB periodically.

### Limits knowledge (human safety)

This system also learns safety/limits guidance from trusted sources into `knowledge_limits.json`.
High-risk messages (self-harm, abuse/violence, emergency cues) trigger guardrail responses using this KB.

Batch learn command:

```powershell
python scripts/learn_limits_kb.py --count 80 --timeout 14
```

Unified continuous learner (relationship + limits):

```powershell
python scripts/background_learning_loop.py --sleep-minutes 180 --relationship-count 24 --limits-count 12
```

`ENABLE_BACKGROUND_LEARNING=1` starts this learning loop automatically in API background at startup.
Set `LIMITS_LEARNING_ON_CHAT=1` to keep limits guidance learning active per conversation without blocking user reply.

### Client Prebuilt Context + Memory

`/v1/chat` now accepts optional `context` in request body:

- `context.model_name`
- `context.behavior_profile`
- `context.prebuilt_prompt`
- `context.user_id` / `context.session_id`
- `context.memory_short` / `context.memory_long`

Server behavior:
- Answers user question first.
- Applies provided prebuilt context as system guidance.
- Uses short-term memory from recent chat history.
- Uses long-term memory from `memory_store.json` (keyed by `user_id` or `session_id`).

Example payload:

```json
{
  "message": "who discovered gravity?",
  "history": [],
  "context": {
    "user_id": "u_101",
    "session_id": "s_abc",
    "model_name": "qwen2.5",
    "behavior_profile": "answer-first, concise, natural",
    "prebuilt_prompt": "Always prioritize direct factual answers.",
    "memory_short": ["user prefers concise replies"],
    "memory_long": ["user asks mixed Hindi/English questions"]
  }
}
```

### 90% target campaign (auto-upgrade + non-repeat questions)

Runs 50 questions, checks quality ratio, and if ratio is below target it upgrades question strategy and runs another non-repeating 50:

```powershell
python scripts/run_90_campaign.py --count 50 --target 90 --max-history 16
```

Outputs:
- `transcripts/campaign_batch_*.txt`
- `transcripts/campaign_batch_*.json`
- `transcripts/campaign_summary_*.json`

Notes:
- Questions are non-repeating inside a campaign run.
- Default seed is auto-generated (`--seed 0`), so each run uses a new question mix.

### 4x50 strict campaign (retry until pass)

Runs 4 successful batches of 50 questions each.  
If a batch is below target, it auto-retries with a fresh non-repeating 50-question set.

```powershell
python scripts/run_4x50_until90.py --runs 4 --count 50 --target 90 --max-history 16
```

Outputs:
- `transcripts/run*_attempt*.txt`
- `transcripts/run*_attempt*.json`
- `transcripts/campaign_4x50_summary_*.json`

### 20-message test + auto-report

Run interactive chat and generate quality report from the saved transcript:

```powershell
.\scripts\run_20_chat_with_report.ps1 -Turns 20 -MaxHistory 120
```

This writes:
- `transcripts/chat_20_YYYYMMDD_HHMMSS.txt`
- `transcripts/chat_report_YYYYMMDD_HHMMSS.json`
- `transcripts/chat_report_YYYYMMDD_HHMMSS.txt`

You can also analyze any existing transcript directly:

```powershell
python scripts/analyze_chat_transcript.py --input-file transcripts\chat_20_YYYYMMDD_HHMMSS.txt
```

## Important tuning variables

- `DEFAULT_TEMPERATURE` and `DEFAULT_TOP_P`: creativity vs stability
- `RESPONSE_CHECKPOINTS`: quality strictness (`2` recommended)
- `FAST_RESPONSE_DEADLINE_SECONDS`: hard per-request reply budget; if model is slow, API falls back fast
- `RUNTIME_START_WAIT_SECONDS`: max time to wait for runtime startup on a request before fast fallback
- `ENABLE_ASYNC_LEARNING_ON_CHAT`: keep learning in background without blocking user response
- `ASYNC_LEARNING_MAX_CONCURRENCY`: async learning parallelism (keep `1` on small CPU)
- `ULTRA_FAST_MODE`: bypass heavy generation path and return fast deterministic replies while learning continues in background
- `HISTORY_USER_TURNS`: memory window size
- `MEMORY_SUMMARY_ITEMS`: compact memory summary size
- `MAX_INPUT_CHARS`, `MAX_OUTPUT_TOKENS`: request/response caps
- `ENABLE_WEB_FALLBACK`: if `1`, weak/unknown factual replies trigger internet lookup
- `WEB_LOOKUP_FOR_FACTS`: if `1`, factual questions prefer web-verified lookup even when model draft looks okay
- `WEB_LOOKUP_TIMEOUT_SECONDS`: timeout for internet lookup requests
- `WEB_CACHE_MAX_ITEMS`: local learned cache size (`knowledge_cache.json`)
- `ENABLE_RELATIONSHIP_LEARNING`: enable relationship-domain KB system
- `RELATIONSHIP_BRIDGE_MODE`: answer-first + subtle relationship bridge for non-relationship questions
- `RELATIONSHIP_LEARNING_ON_CHAT`: keep relationship KB learning active for new/missed queries
- `RELATIONSHIP_KB_PATH`: file path of relationship KB JSON
- `RELATIONSHIP_KB_MAX_ITEMS`: max entries stored in relationship KB
- `RELATIONSHIP_LEARNING_TIMEOUT_SECONDS`: timeout for relationship web learning calls
- `ENABLE_LIMITS_GUARDRAILS`: enable high-risk human-safety guardrail flow
- `LIMITS_LEARNING_ON_CHAT`: keep high-risk limits KB learning active per conversation
- `LIMITS_KB_PATH`: file path of limits KB JSON
- `LIMITS_KB_MAX_ITEMS`: max entries stored in limits KB
- `LIMITS_LEARNING_TIMEOUT_SECONDS`: timeout for limits web learning calls
- `ENABLE_BACKGROUND_LEARNING`: always-on background learning during API runtime
- `BACKGROUND_LEARNING_SLEEP_MINUTES`: cycle gap for background learning
- `BACKGROUND_RELATIONSHIP_COUNT`: relationship questions learned per cycle
- `BACKGROUND_LIMITS_COUNT`: limits questions learned per cycle
- `ENABLE_LONG_TERM_MEMORY`: persistent long-memory storage enable/disable
- `MEMORY_STORE_PATH`: long-memory JSON file path
- `MEMORY_STORE_MAX_USERS`: max user/session memory buckets
- `MEMORY_STORE_MAX_ITEMS_PER_USER`: per-user/session long-memory cap
- `MEMORY_STORE_READ_ITEMS`: long-memory items injected per request
- `MEMORY_STORE_WRITE_ITEMS`: max memory items persisted per request

## Security controls

- Required `x-api-key` and `x-client-secret` for all chat endpoints
- Constant-time key comparison
- Rate limiter (`RATE_LIMIT_RPM`, `RATE_LIMIT_BURST`)
- Internal key separation between API wrapper and llama-server

## Render deployment

`render.yaml` is configured for deployment:

- Install deps from `requirements-api.txt`
- Run bootstrap script
- Start app with `uvicorn api_service:app`
- Health check path: `/health`

## GitHub -> Render (Recommended)

1) Initialize git and first commit:

```powershell
git init -b main
git add .
git commit -m "Initial Render-ready API setup"
```

2) Create empty GitHub repo, then connect and push:

```powershell
git remote add origin https://github.com/<your-username>/<your-repo>.git
git push -u origin main
```

3) In Render:
- `New +` -> `Blueprint`
- Connect GitHub repo
- Select this project (Render will read `render.yaml`)
- Deploy

4) After deploy, call API with both headers:
- `x-api-key`
- `x-client-secret`

Notes:

- Starter/free CPUs are slower for inference
- First boot can take longer (runtime/model prep)
- Keep API keys private and rotate periodically
