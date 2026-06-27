"""OpenAI-compatible relay (中转) — legacy billing endpoints. Two calls:
  GET {base}/v1/dashboard/billing/subscription  → hard_limit_usd (total cap, USD)
  GET {base}/v1/dashboard/billing/usage?start_date&end_date → total_usage (CENTS)
remaining_usd = hard_limit_usd - total_usage/100.  end_date must be today+1 (exclusive).
"""

from datetime import date, timedelta

import httpx

from ..models import ChannelConfig, QuotaResult
from .base import QuotaAdapter


class OpenAIRelayAdapter(QuotaAdapter):
    async def fetch_quota(self, ch: ChannelConfig, client: httpx.AsyncClient) -> QuotaResult:
        if not ch.api_key:
            return self._err(ch, "API_KEY not configured")
        if not ch.base_url:
            return self._err(ch, "BASE_URL required for openai-relay")

        base = ch.base_url.rstrip("/")
        hdr = {"Authorization": f"Bearer {ch.api_key}"}
        today = date.today()
        start = today.replace(day=1)  # first of current month
        end = today + timedelta(days=1)  # range is exclusive on end → today+1

        sub_resp = await client.get(f"{base}/v1/dashboard/billing/subscription", headers=hdr)
        if sub_resp.status_code != 200:
            return self._err(
                ch, f"subscription HTTP {sub_resp.status_code}: {sub_resp.text[:200]}"
            )
        sub = sub_resp.json() or {}
        hard_limit = float(sub.get("hard_limit_usd") or 0.0)
        access_until = sub.get("access_until")

        usage_resp = await client.get(
            f"{base}/v1/dashboard/billing/usage",
            params={"start_date": start.isoformat(), "end_date": end.isoformat()},
            headers=hdr,
        )
        if usage_resp.status_code != 200:
            return self._err(
                ch, f"usage HTTP {usage_resp.status_code}: {usage_resp.text[:200]}"
            )
        usage = usage_resp.json() or {}
        used_usd = float(usage.get("total_usage") or 0.0) / 100.0
        remaining = hard_limit - used_usd
        percent = (used_usd / hard_limit * 100.0) if hard_limit > 0 else None
        reset = str(access_until) if access_until else None
        return self._ok(
            ch,
            balance=remaining,
            total=hard_limit,
            used=used_usd,
            unit="USD",
            percent=percent,
            reset_time=reset,
        )
