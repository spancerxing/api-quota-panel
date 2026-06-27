"""Password auth: timing-safe compare, in-memory login rate-limit, signed session
cookie (via Starlette SessionMiddleware in main.py), and the require_auth dependency."""

import hashlib
import hmac
import time
from collections import defaultdict

from fastapi import HTTPException, Request, status
from pydantic import BaseModel

from .config import settings

# In-memory failed-login tracking per IP. Fine for a single-process local panel.
_attempts: dict[str, list[float]] = defaultdict(list)
_WINDOW = 600  # seconds
_MAX_FAILS = 5


class LoginRequest(BaseModel):
    password: str


def verify_password(pw: str) -> bool:
    """Timing-safe compare. PANEL_PASSWORD_HASH (sha-256 hex) takes precedence if set."""
    if settings.panel_password_hash:
        digest = hashlib.sha256(pw.encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, settings.panel_password_hash)
    return hmac.compare_digest(pw, settings.panel_password or "") if settings.panel_password else False


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit_ok(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _attempts[ip] if now - t < _WINDOW]
    _attempts[ip] = recent
    return len(recent) < _MAX_FAILS


def record_fail(ip: str) -> None:
    _attempts[ip].append(time.time())


async def require_auth(request: Request) -> None:
    if not request.session.get("authed"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
