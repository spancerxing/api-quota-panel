"""硅基流动 (SiliconFlow) — official balance endpoint."""

import httpx

from ..models import ChannelConfig, QuotaResult
from .base import QuotaAdapter

DEFAULT_BASE = "https://api.siliconflow.cn"


class SiliconFlowAdapter(QuotaAdapter):
    async def fetch_quota(self, ch: ChannelConfig, client: httpx.AsyncClient) -> QuotaResult:
        if not ch.api_key:
            return self._err(ch, "API_KEY not configured")
        base = (ch.base_url or DEFAULT_BASE).rstrip("/")
        resp = await client.get(
            f"{base}/v1/user/info",
            headers={"Authorization": f"Bearer {ch.api_key}"},
        )
        if resp.status_code != 200:
            return self._err(ch, f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = (resp.json() or {}).get("data") or {}
        total = data.get("totalBalance")
        if total is None:
            # balance = gift-only; chargeBalance = topped-up; totalBalance = usable total.
            return self._err(ch, f"totalBalance missing; keys={list(data.keys())}")
        try:
            balance = float(total)
        except (TypeError, ValueError):
            return self._err(ch, f"totalBalance not numeric: {total!r}")
        return self._ok(ch, balance=balance, unit="CNY")
