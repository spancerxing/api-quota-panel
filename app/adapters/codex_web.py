"""Codex (ChatGPT Web) — OAuth access token + wham/usage endpoint.

Uses the same internal API as the ChatGPT web app / Codex CLI:
  GET https://chatgpt.com/backend-api/wham/usage
  Authorization: Bearer <chatgpt_access_token>
  User-Agent: Mozilla/5.0 ...
  Chatgpt-Account-Id: <account_id>  (optional, from .env)

Response: rate_limit.primary_window.used_percent (5h quota) and
         rate_limit.secondary_window.used_percent (weekly quota).

Token refresh is attempted automatically on 401 if REFRESH_TOKEN is configured.
"""

import httpx

from ..models import ChannelConfig, QuotaResult
from ..token_refresh import refresh_codex_token
from .base import QuotaAdapter

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CHATGPT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)


class CodexWebAdapter(QuotaAdapter):
    async def fetch_quota(self, ch: ChannelConfig, client: httpx.AsyncClient) -> QuotaResult:
        if not ch.api_key:
            return self._err(ch, "ACCESS_TOKEN not configured (paste from ~/.codex/auth.json)")

        headers = {
            "Authorization": f"Bearer {ch.api_key}",
            "User-Agent": CHATGPT_UA,
            "Referer": "https://chatgpt.com/",
            "Accept": "application/json",
        }
        if ch.account_id:
            headers["ChatGPT-Account-Id"] = ch.account_id

        resp = await client.get(USAGE_URL, headers=headers)

        # Auto-refresh on 401 if refresh_token configured
        if resp.status_code == 401 and ch.refresh_token:
            try:
                new = await refresh_codex_token(ch.refresh_token, client)
                ch.api_key = new["access_token"]
                headers["Authorization"] = f"Bearer {ch.api_key}"
                resp = await client.get(USAGE_URL, headers=headers)
            except Exception as exc:
                return self._err(ch, f"token refresh failed: {exc}")

        if resp.status_code != 200:
            return self._err(ch, f"HTTP {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        rate_limit = body.get("rate_limit") or {}
        plan_type = body.get("plan_type") or "?"

        primary = rate_limit.get("primary_window") or {}
        secondary = rate_limit.get("secondary_window") or {}

        def norm_used(w: dict) -> float:
            p = w.get("used_percent")
            return float(p) if p is not None else None

        def norm_reset(w: dict) -> str:
            r = w.get("reset_at") or w.get("reset_after_seconds")
            if r:
                # reset_at is Unix ts in seconds; reset_after_seconds is relative
                return str(r)
            return None

        p_used = norm_used(primary)
        s_used = norm_used(secondary)
        p_reset = norm_reset(primary)
        s_reset = norm_reset(secondary)

        # Use primary (5h) as the main percent, show secondary as sub info
        if p_used is not None:
            percent = 100.0 - p_used
            kw = {
                "percent": percent,
                "unit": "%",
                "used": p_used,
                "total": 100,
                "reset_time": p_reset,
            }
            # Include secondary info if available (as used%, not balance)
            if s_used is not None:
                kw["balance"] = round(100.0 - s_used, 1)
                kw["unit"] = "%"
        elif s_used is not None:
            percent = 100.0 - s_used
            kw = {"percent": percent, "unit": "%", "used": s_used, "total": 100, "reset_time": s_reset}
        else:
            # No quota window data — show plan type only
            return self._ok(ch, unit="None", error=f"plan_type={plan_type}, no usage windows")

        return self._ok(ch, **kw)