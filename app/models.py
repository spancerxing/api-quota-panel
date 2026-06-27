"""Pydantic models. QuotaResult is the ONLY shape sent to the frontend — audited
to contain no API keys, no raw provider responses, no headers. Only numbers/status."""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ChannelConfig(BaseModel):
    """One configured channel, built from .env (<NAME>_*) in config.py."""

    name: str  # env NAME, e.g. "SILICONFLOW"
    enabled: bool
    type: str  # adapter type: siliconflow | openai-relay | google-oauth | codex-web | pioneer | unsupported
    api_key: Optional[str] = None  # API key or OAuth access_token
    base_url: Optional[str] = None
    label: str  # display name
    refresh_token: Optional[str] = None  # OAuth refresh_token (codex-web, google-oauth)
    account_id: Optional[str] = None  # ChatGPT-Account-Id (codex-web)
    project_id: Optional[str] = None  # GCP project_id (google-oauth)


class GroupQuota(BaseModel):
    """One sub-quota group within a multi-quota channel.

    Used by adapters that return several independent quotas under a single
    channel (e.g. Antigravity returns per-model groups: 'Gemini' + 'Claude & GPT').
    """

    label: str  # group header, e.g. "Gemini" or "Claude & GPT"
    percent: float  # USED 0-100 (drives bar fill + color, like top-level percent)
    reset_time: Optional[str] = None  # provider-specific reset hint (ISO duration or timestamp)
    models: list[str] = []  # member model names displayed in the group header sub-line


class QuotaResult(BaseModel):
    """Normalized quota result for one channel. Sent to the frontend as-is."""

    id: str  # channel NAME
    label: str
    type: str
    supported: bool
    status: Literal["ok", "error", "unsupported"]
    balance: Optional[float] = None  # remaining (currency or tokens)
    used: Optional[float] = None
    total: Optional[float] = None
    unit: Optional[str] = None  # "CNY" | "USD" | "%"
    percent: Optional[float] = None  # used % 0-100 (drives color)
    reset_time: Optional[str] = None  # ISO8601 next reset
    error: Optional[str] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Multi-quota channel sub-display (e.g. Antigravity Gemini/Claude&GPT groups).
    # When set, frontend renders one sub-bar per group instead of the flat top-level bar.
    groups: Optional[list[GroupQuota]] = None
