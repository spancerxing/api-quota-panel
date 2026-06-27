"""Google OAuth (Antigravity / Gemini) — v1internal:fetchAvailableModels.

Uses the same internal API as Antigravity CLI:
  POST {base}/v1internal:fetchAvailableModels
  Authorization: Bearer <google_oauth_access_token>
  User-Agent: antigravity/<ver> <os>/<arch>
  Content-Type: application/json
  {"project": "<project_id>"}

Response: models[<name>].quotaInfo.remainingFraction (0-1) + resetTime.

The adapter groups models by family and returns one sub-quota per group
(matching Antigravity CLI's /usage display):
  - Gemini:         gemini-*
  - Claude & GPT:   claude-* | gpt-*

Per group, the most conservative remainingFraction (= highest USED %) is shown.
The top-level percent is the max USED across groups so the card border color
reflects the worst group.

Token refresh is attempted automatically on 401 if REFRESH_TOKEN is configured.
"""

import httpx

from ..models import ChannelConfig, GroupQuota, QuotaResult
from ..token_refresh import refresh_google_token
from .base import QuotaAdapter

DEFAULT_BASE = "https://cloudcode-pa.googleapis.com"
FETCH_AVAILABLE_MODELS_PATH = "v1internal:fetchAvailableModels"
# Hard-coded UA matching the Antigravity CLI.
_DEFAULT_UA = "antigravity/cli/1.0.8 darwin/arm64"


# (group_label, predicate(name_lower))
# Order = display order. Edit here to change the grouping.
_GROUPS: list[tuple[str, "callable"]] = [
    ("Gemini", lambda n: "gemini" in n),
    ("Claude & GPT", lambda n: "claude" in n or "gpt" in n),
]


def _pretty_model(key: str) -> str:
    """models/gemini-2.5-flash → 'gemini-2.5-flash' (strip models/ prefix)."""
    return key.split("/", 1)[-1] if "/" in key else key


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

        groups_out: list[GroupQuota] = []
        overall_used: float | None = None  # max USED across groups (drives card border)

        for label, predicate in _GROUPS:
            # Collect (remainingFraction, resetTime) for all models in this group.
            entries: list[tuple[str, float, str]] = []
            for name, info in models.items():
                if not predicate(name.lower()):
                    continue
                qi = (info or {}).get("quotaInfo") or {}
                frac = qi.get("remainingFraction")
                if frac is None:
                    continue
                entries.append((name, float(frac), qi.get("resetTime") or ""))

            if not entries:
                continue

            # Most conservative = lowest remainingFraction (highest USED).
            best_name, best_remaining, best_reset = min(entries, key=lambda e: e[1])
            used_pct = round((1.0 - best_remaining) * 100.0, 1)

            # Sorted, deduplicated member names (display only).
            member_names = sorted({_pretty_model(n) for n, _, _ in entries})

            groups_out.append(
                GroupQuota(
                    label=label,
                    percent=used_pct,
                    reset_time=best_reset or None,
                    models=member_names,
                )
            )

            if overall_used is None or used_pct > overall_used:
                overall_used = used_pct

        if not groups_out:
            return self._err(
                ch,
                f"no quotaInfo in any model; keys={list(models.keys())[:5]}",
            )

        # Top-level percent = max USED across groups (drives card border color).
        return self._ok(ch, percent=overall_used, groups=groups_out, unit="%")