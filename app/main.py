"""FastAPI app: session middleware, static mount, auth + quota routes."""

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .adapters import get_adapter
from .aggregator import fetch_all, fetch_one
from .auth import LoginRequest, client_ip, rate_limit_ok, record_fail, require_auth, verify_password
from .cache import TTLCache
from .config import settings, write_env_updates
from .models import QuotaResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("quota-panel")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Session cookie signing key. Fall back to an ephemeral random secret so the app
# still boots when the placeholder is left in place (sessions just won't persist).
if settings.using_default_secret:
    _session_secret = secrets.token_hex(32)
else:
    _session_secret = settings.panel_session_secret


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.using_default_secret:
        log.warning(
            "PANEL_SESSION_SECRET unset/placeholder — using ephemeral secret; "
            "sessions reset on restart. Set a real secret in .env."
        )
    if settings.using_default_password:
        log.warning("PANEL_PASSWORD unset/placeholder — set a real password in .env.")
    yield


app = FastAPI(title="API Quota Panel", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    same_site="lax",
    https_only=settings.panel_cookie_secure,
    session_cookie="session",
    max_age=14 * 24 * 3600,
)

_cache = TTLCache(settings.cache_ttl)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/login")
async def login(req: LoginRequest, request: Request):
    ip = client_ip(request)
    if not rate_limit_ok(ip):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts, try later."
        )
    if not verify_password(req.password):
        record_fail(ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid password.")
    request.session["authed"] = True
    return {"ok": True}


@app.post("/api/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/quota")
async def get_quota(refresh: int = 0, _: None = Depends(require_auth)) -> list[QuotaResult]:
    enabled = settings.enabled_channels()
    if refresh:
        # bypass cache: re-fetch all channels in parallel
        results = await fetch_all(enabled, settings.adapter_timeout)
        _cache.set_all(results)
    else:
        # incremental: only refetch channels whose cached entry is stale or missing,
        # so a single slow/failed channel doesn't punish the others.
        stale = [c for c in enabled if _cache.is_stale(c.name)]
        if stale:
            results = await fetch_all(stale, settings.adapter_timeout)
            for r in results:
                _cache.set_one(r)
    return _cache.all()


@app.get("/api/quota/{channel_name}")
async def get_quota_one(channel_name: str, _: None = Depends(require_auth)) -> QuotaResult:
    """Refresh a single channel on demand and update its cache entry in place."""
    ch = next((c for c in settings.channels if c.name.upper() == channel_name.upper()), None)
    if ch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Channel {channel_name} not found")
    result = await fetch_one(ch, settings.adapter_timeout)
    _cache.set_one(result)
    return result


@app.post("/api/refresh/{channel_name}")
async def refresh_token(channel_name: str, _: None = Depends(require_auth)) -> dict:
    """Manually trigger OAuth token refresh for a channel.
    Requires refresh_token to be configured in .env."""
    ch = next((c for c in settings.channels if c.name.upper() == channel_name.upper()), None)
    if ch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Channel {channel_name} not found")
    if not ch.refresh_token:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="REFRESH_TOKEN not configured for this channel")
    if ch.type not in ("codex-web", "google-oauth"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Channel type {ch.type} does not support token refresh")

    from . import token_refresh as tr
    import httpx

    transport = httpx.AsyncHTTPTransport(proxy=None)
    try:
        async with httpx.AsyncClient(transport=transport, timeout=30) as client:
            if ch.type == "codex-web":
                new = await tr.refresh_codex_token(ch.refresh_token, client)
            else:  # google-oauth
                new = await tr.refresh_google_token(ch.refresh_token, client)

        ch.api_key = new["access_token"]
        rotated_refresh = new["refresh_token"] != ch.refresh_token
        if rotated_refresh:
            ch.refresh_token = new["refresh_token"]

        # Persist the rotated tokens to .env and hot-reload settings so the rest of
        # the app picks up the new values without a restart. The .env file is the
        # source of truth across restarts; settings.reload() rebuilds in-memory
        # state from it. Atomic write protects against half-written files.
        updates: dict[str, str] = {f"{ch.name}_API_KEY": new["access_token"]}
        if rotated_refresh and new["refresh_token"]:
            updates[f"{ch.name}_REFRESH_TOKEN"] = new["refresh_token"]
        try:
            written = write_env_updates(settings.env_path, updates)
            settings.reload()
            log.info("Persisted rotated tokens for %s to %s (keys=%s)", ch.name, settings.env_path, written)
        except OSError as exc:
            log.warning("Could not persist rotated tokens to %s: %s", settings.env_path, exc)

        # Only this channel's cached entry is stale — invalidate it; let other channels
        # keep their still-fresh cached results.
        _cache.invalidate(ch.name)
        log.info("Token refreshed for channel %s", ch.name)
        return {"ok": True, "channel": ch.name, "token_refreshed": True}
    except Exception as exc:
        log.error("Token refresh failed for %s: %s", ch.name, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Token refresh failed: {exc}")
