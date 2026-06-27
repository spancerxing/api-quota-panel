"""Pioneer.ai — billing plan info endpoint.

  GET https://api.pioneer.ai/billing/plan-info
  X-API-Key: <api_key>

Response shape:
  {
    "payment_plan": "hobby",
    "credit_limit": 3000.0,
    "total_usage": 160.853,
    "remaining_credits": 2839.147,
    "exceeds_limit": false
  }

Falls back to /billing/billing-status if plan-info is missing fields.
"""

import httpx

from ..models import ChannelConfig, QuotaResult
from .base import QuotaAdapter

_DEFAULT_BASE = "https://api.pioneer.ai"
_PLAN_INFO_PATH = "/billing/plan-info"
_BILLING_STATUS_PATH = "/billing/billing-status"


class PioneerAdapter(QuotaAdapter):
    async def fetch_quota(self, ch: ChannelConfig, client: httpx.AsyncClient) -> QuotaResult:
        if not ch.api_key:
            return self._err(ch, "API_KEY not configured")

        base = (ch.base_url or _DEFAULT_BASE).rstrip("/")
        headers = {"X-API-Key": ch.api_key}

        # 1) /billing/plan-info — 优先路径，字段最干净
        plan = await self._get_json(client, f"{base}{_PLAN_INFO_PATH}", headers)
        if plan is not None and "remaining_credits" in plan:
            return self._format(ch, plan)

        # 2) /billing/billing-status — 备选路径
        status = await self._get_json(client, f"{base}{_BILLING_STATUS_PATH}", headers)
        if status is not None and "free_tier_remaining" in status:
            return self._format(
                ch,
                {
                    "credit_limit": status.get("credit_limit"),
                    "total_usage": status.get("total_usage"),
                    "remaining_credits": status.get("free_tier_remaining"),
                    "payment_plan": status.get("payment_plan"),
                },
            )

        return self._err(
            ch,
            "unable to read billing status from /billing/plan-info or /billing/billing-status",
        )

    @staticmethod
    async def _get_json(client: httpx.AsyncClient, url: str, headers: dict) -> dict | None:
        try:
            resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            return {"_error": f"{type(exc).__name__}: {exc}"}
        if resp.status_code == 402:
            return {"_error": "402 Payment Required — account out of credits"}
        if resp.status_code != 200:
            return {"_error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return {"_error": f"invalid json: {exc}"}
        if not isinstance(data, dict):
            return {"_error": f"unexpected payload type: {type(data).__name__}"}
        return data

    def _format(self, ch: ChannelConfig, body: dict) -> QuotaResult:
        if body.get("_error"):
            return self._err(ch, body["_error"])

        def _f(key: str) -> float | None:
            v = body.get(key)
            if v is None or v == "":
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        # Pioneer 接口返回的是「分」单位（与 Stripe 一致），展示前除以 100 换算成美元。
        remaining = _f("remaining_credits")
        total = _f("credit_limit")
        used = _f("total_usage")
        plan = body.get("payment_plan")

        if remaining is None:
            return self._err(ch, f"missing remaining_credits; keys={list(body.keys())}")

        remaining /= 100.0
        kwargs: dict = {"balance": round(remaining, 4), "unit": "USD"}
        if used is not None:
            used = round(used / 100.0, 4)
            kwargs["used"] = used
        if total is not None:
            total = round(total / 100.0, 4)
            kwargs["total"] = total
            # percent 表示「已用百分比」，用于驱动卡片颜色（levelFor）。
            if total > 0 and used is not None:
                kwargs["percent"] = round(used / total * 100.0, 1)
        if plan:
            kwargs["error"] = f"plan={plan}"

        return self._ok(ch, **kwargs)
