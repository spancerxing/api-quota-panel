"""Adapter base class + shared result builders."""

from abc import ABC, abstractmethod

import httpx

from ..models import ChannelConfig, QuotaResult


class QuotaAdapter(ABC):
    """One adapter per provider TYPE. fetch_quota must return a QuotaResult and
    never raise for expected failures — convert them to status='error' so a single
    failing channel doesn't break the aggregate response."""

    @abstractmethod
    async def fetch_quota(self, ch: ChannelConfig, client: httpx.AsyncClient) -> QuotaResult:
        ...

    # --- result builders (updated_at auto-stamped via model default_factory) ---
    @staticmethod
    def _ok(ch: ChannelConfig, **kw) -> QuotaResult:
        return QuotaResult(
            id=ch.name, label=ch.label, type=ch.type, supported=True, status="ok", **kw
        )

    @staticmethod
    def _err(ch: ChannelConfig, msg: str) -> QuotaResult:
        return QuotaResult(
            id=ch.name, label=ch.label, type=ch.type, supported=True, status="error", error=msg
        )

    @staticmethod
    def _unsupported(ch: ChannelConfig) -> QuotaResult:
        return QuotaResult(
            id=ch.name, label=ch.label, type=ch.type, supported=False, status="unsupported"
        )
