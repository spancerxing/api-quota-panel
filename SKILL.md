---
name: api-quota-panel
description: Web dashboard for viewing AI API quota/credit balances across multiple providers
---

# API Quota Panel (api-quota-panel)

Python + FastAPI web dashboard that aggregates API quota/credit balances from multiple AI providers. Single-page vanilla JS frontend, no build step, deployable via Docker.

## Tech Stack

- **Python 3.14+** with FastAPI, httpx, uvicorn
- **Pydantic** for models and validation
- **Starlette SessionMiddleware** for signed session cookies
- **Vanilla JS** frontend (no framework/build step)
- **Docker** (python:3.14-slim + docker-compose)

## Architecture

### Adapter Pattern

Each AI provider is implemented as an adapter class under `app/adapters/`, registered by `TYPE` string in `app/adapters/__init__.py`:

```python
REGISTRY = {
    "siliconflow": SiliconFlowAdapter,
    "codex-web": CodexWebAdapter,
    "google-oauth": GoogleOAuthAdapter,
    "openai-relay": OpenAIRelayAdapter,
    "pioneer": PioneerAdapter,
    "unsupported": UnsupportedAdapter,
}
```

Adapters inherit `QuotaAdapter` and implement:

```python
async def fetch_quota(self, ch: ChannelConfig, client: httpx.AsyncClient) -> QuotaResult
```

**Rules:**
- Always return a `QuotaResult`, never raise. Convert errors to `status="error"`.
- Use `self._ok(ch, ...)`, `self._err(ch, msg)`, `self._unsupported(ch)` helpers.
- Use `ch.api_key` for the primary secret (API key or OAuth access_token).
- Use `ch.refresh_token` for optional OAuth refresh_token (codex-web, google-oauth).
- Use `ch.base_url` for custom API endpoints (openai-relay) or optional override.
- Use `ch.account_id` / `ch.project_id` for provider-specific identifiers.

### Data Flow (Incremental Fetch)

1. User logs in with password → server sets signed HttpOnly session cookie
2. Frontend calls `GET /api/quota` (with cookie) every 5 min
3. Server builds `stale = [c for c in enabled if _cache.is_stale(c.name)]`
4. If any channels are stale/missing → `fetch_all(stale, timeout)` in parallel; merge with still-fresh cache entries
5. If nothing stale → return cached results as-is (no upstream call)
6. `?refresh=1` bypasses cache and re-fetches every enabled channel in parallel
7. Each adapter returns a `QuotaResult` (error-isolated — one failure doesn't block others)
8. Results cached per-channel for `CACHE_TTL_SECONDS` (default 120s)

### Per-channel Refresh

- `GET /api/quota/{channel_name}` — runs a single channel's adapter, replaces its cache entry via `cache.set_one`, returns it. Frontend wires each card's ↻ button to this.
- `POST /api/refresh/{channel}` — OAuth token rotation. See below.

### Token Refresh & Persistence (OAuth-only types)

When `ch.refresh_token` is configured for `codex-web` or `google-oauth`:

1. Adapter calls provider API with `ch.api_key`
2. If 401 → calls `refresh_codex_token` / `refresh_google_token` (in `app/token_refresh.py`)
3. On success: updates `ch.api_key` (and `ch.refresh_token` if rotated) **in memory**
4. **Atomic write to `.env`** via `app.config.write_env_updates(env_path, updates)` — temp file + `os.replace()`, section-aware placement of new keys
5. **`settings.reload()`** re-reads `.env` into `os.environ` (file wins, `override=True`) and rebuilds `self.channels[:]` in place
6. **Cache**: only this channel's entry is invalidated via `cache.invalidate(ch.name)`; other channels keep their fresh cached values

Manual trigger: `POST /api/refresh/{channel}` performs steps 2–6 unconditionally (no upstream 401 needed).

### `.env` Hot Reload Semantics

- **Initial import**: `_load_dotenv()` only sets env vars that aren't already set — shell / `env_file` wins.
- **`Settings.reload()`**: `_load_dotenv(override=True)` — `.env` file wins, since we just rewrote it.
- For Docker: `env_file` is read at container start, so after editing `.env` on the host, **`docker compose restart panel`** is required to pick up the new file. There's no inotify watcher.
- The `.env` path is `Settings.env_path`, overridable via the `PANEL_ENV_FILE` env var.

## File Layout

```
api-quota-panel/
├── app/
│   ├── main.py              FastAPI routes, middleware, static mount
│   ├── config.py            .env loading, Settings.reload(), write_env_updates()
│   ├── models.py            ChannelConfig + QuotaResult (pydantic)
│   ├── auth.py              Password verify, rate-limit, require_auth
│   ├── cache.py             In-memory TTL cache: is_stale/invalidate/set_one/set_all/all
│   ├── aggregator.py        Parallel fetch_all + fetch_one with timeout isolation
│   ├── token_refresh.py     Codex + Antigravity Google OAuth token refresh helpers
│   └── adapters/
│       ├── base.py          QuotaAdapter ABC + _ok/_err/_unsupported
│       ├── __init__.py      REGISTRY + get_adapter()
│       ├── siliconflow.py       GET /v1/user/info → totalBalance (CNY)
│       ├── openai_relay.py      legacy billing subscription+usage (USD)
│       ├── codex_web.py         wham/usage → used_percent (ChatGPT Web)
│       ├── google_oauth.py      v1internal:fetchAvailableModels → remainingFraction (Antigravity UA: antigravity/cli/1.0.8 darwin/arm64)
│       ├── pioneer.py           /billing/plan-info → remaining_credits/credit_limit (USD, cents /100)
│       └── unsupported.py       Returns supported=False for channels with no endpoint
├── static/
│   ├── index.html           SPA shell with login and dashboard views
│   ├── app.js               Fetch /api/quota → render card grid; each card has its own ↻ button bound to /api/quota/{id}; auto-refresh every 5 min
│   └── styles.css           Responsive grid, dark/light mode, status colors
├── check.py                 CLI: test one or all adapters (python check.py [NAME|all])
├── example.env              Template with placeholders (committed)
├── requirements.txt
├── Dockerfile               python:3.14-slim, non-root, healthcheck
├── docker-compose.yml
├── .gitignore
├── .dockerignore
├── README.md
└── SKILL.md
```

## Security

### Audit Guarantees

- `QuotaResult` is the **only** shape sent to the frontend. Its fields are audited to exclude API keys, raw provider responses, and HTTP headers.
- `.env` is in both `.gitignore` and `.dockerignore`; the image never carries live credentials.
- Token refresh exceptions are logged with the channel name and a short error type, not the request body.
- The 502 response from `/api/refresh/{channel}` includes `str(exc)` — most OAuth errors don't echo the request, but if a provider ever does, the response would leak it. Verify in tests.

### Hard Rules

- **Never** `cat` or `Read` a user's real `.env` file from a shell or test script.
- **Never** `print(os.environ)` or `print(ch.api_key)` in any script — use synthetic `fake_*` strings in `/tmp/test-*.env` for fixtures.
- **Never** commit a real token to git or paste it in a chat/log. If you do, follow the incident response above immediately.

## Conventions

### Code Style
- Type hints everywhere (`Optional[str]`, `list[QuotaResult]`, etc.)
- Imports: stdlib → third-party → local; absolute for exports, relative for siblings
- Docstrings: one-line `"""description."""` for every module and public function
- Error messages in Chinese for user-facing paths (适配器错误), English for internal logs

### Adding a New Provider
1. Create `app/adapters/<name>.py` with a class inheriting `QuotaAdapter`
2. Add to `REGISTRY` in `app/adapters/__init__.py`
3. Add channel name to `_CHANNEL_NAMES` in `app/config.py`
4. Add template config section in `example.env`
5. Test: `python check.py <NAME>`

### Commit / PR Checklist
- [ ] New adapter: `check.py <NAME>` returns expected result
- [ ] No API keys in response (`/api/quota` response audited)
- [ ] `example.env` updated with new type explanation
- [ ] `README.md` channel table updated
- [ ] No real tokens committed; `.env` only in `.gitignore`
- [ ] No token values in test output, logs, or commit messages

## Useful Commands

```bash
# List configured channels
python check.py

# Test one adapter
python check.py SILICONFLOW

# Test all enabled adapters in parallel
python check.py all

# Run dev server
uvicorn app.main:app --reload --port 9876

# After editing .env manually, restart the container (env_file is not hot-reloaded)
docker compose restart panel
```