"""Google OAuth (Antigravity / Gemini) — v1internal:fetchAvailableModels.

Uses the same internal API as Antigravity CLI:
  POST {base}/v1internal:fetchAvailableModels
  Authorization: Bearer <google_oauth_access_token>
  User-Agent: antigravity/<ver> <os>/<arch>
  Content-Type: application/json
  {"project": "<project_id>"}

Response: models[<name>].quotaInfo.remainingFraction (0-1) + resetTime.

Filters to models named gemini-* / claude-* (same as cockpit-tools).
Token refresh is attempted automatically on 401 if REFRESH_TOKEN is configured.
"""

import httpx

from ..models import ChannelConfig, QuotaResult
from ..token_refresh import refresh_google_token
from .base import QuotaAdapter

DEFAULT_BASE = "https://cloudcode-pa.googleapis.com"
FETCH_AVAILABLE_MODELS_PATH = "v1internal:fetchAvailableModels"
# Hard-coded UA matching the Antigravity CLI.
_DEFAULT_UA = "antigravity/cli/1.0.8 darwin/arm64"


class GoogleOAuthAdapter(QuotaAdapter):
    async def fetch_quota(self, ch: ChannelConfig, client: httpx.AsyncClient) -> QuotaResult:
        if not ch.api_key:
            return self._err(ch, "ACCESS_TOKEN not configured (paste from ~/.gemini/ or antigravity settings)")

        base = (ch.base_url or DEFAULT_BASE).rstrip("/")
        url = f"{base}/{FETCH_AVAILABLE_MODELS_PATH}"
        payload = {}
        if ch.project_id:
            payload["project"] = ch.project_id

        headers = {
            "Authorization": f"Bearer {ch.api_key}",
            "User-Agent": _DEFAULT_UA,
            "Content-Type": "application/json",
        }

        resp = await client.post(url, json=payload, headers=headers)

        # Auto-refresh on 401 if refresh_token configured
        if resp.status_code == 401 and ch.refresh_token:
            try:
                new = await refresh_google_token(ch.refresh_token, client)
                ch.api_key = new["access_token"]
                headers["Authorization"] = f"Bearer {ch.api_key}"
                resp = await client.post(url, json=payload, headers=headers)
            except Exception as exc:
                return self._err(ch, f"token refresh failed: {exc}")

        if resp.status_code != 200:
            return self._err(ch, f"HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        models = data.get("models") or {}
        if not models:
            return self._err(ch, "no models in response")

        # Find the best model with quotaInfo.
        # Prefer gemini/claude models; among those, pick the most conservative (lowest remainingFraction).
        best_name = None
        best_frac = None
        best_reset = None

        gemini_or_claude = lambda name: "gemini" in name.lower() or "claude" in name.lower()
        has_found_preferred = False

        for name, info in models.items():
            qi = (info or {}).get("quotaInfo") or {}
            frac = qi.get("remainingFraction")
            if frac is None:
                continue
            frac_f = float(frac)
            reset = qi.get("resetTime") or ""

            is_preferred = gemini_or_claude(name)
            if best_frac is None:
                best_name, best_frac, best_reset = name, frac_f, reset
                has_found_preferred = is_preferred
            elif is_preferred and not has_found_preferred:
                # First preferred model found → switch
                best_name, best_frac, best_reset = name, frac_f, reset
                has_found_preferred = True
            elif is_preferred == has_found_preferred and frac_f < best_frac:
                # Same category, record the more conservative remaining
                best_name, best_frac, best_reset = name, frac_f, reset

        if best_frac is None:
            return self._err(ch, f"no quotaInfo in any model; model keys={list(models.keys())[:5]}")

        percent = best_frac * 100.0
        return self._ok(ch, percent=round(percent, 1), reset_time=best_reset, unit="%")