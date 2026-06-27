"""No quota endpoint available — card shows '暂不支持查询'."""

import httpx

from ..models import ChannelConfig, QuotaResult
from .base import QuotaAdapter


class UnsupportedAdapter(QuotaAdapter):
    async def fetch_quota(self, ch: ChannelConfig, client: httpx.AsyncClient) -> QuotaResult:
        return self._unsupported(ch)
