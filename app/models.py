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


class BucketQuota(BaseModel):
    """One time-window bucket inside a group.

    Antigravity returns multiple buckets per group (Weekly + 5h Session);
    each bucket is its own progress bar with its own reset hint.
    """

    label: str  # "Weekly Limit" / "5h Limit" / "Session"
    percent: float  # USED 0-100 (drives bar fill + color)
    reset_time: Optional[str] = None  # ISO 8601 duration or epoch seconds
    description: Optional[str] = None  # provider prose like "Resets weekly on Tuesdays"


class GroupQuota(BaseModel):
    """One sub-quota group within a multi-quota channel.

    Antigravity returns two groups (Gemini / Claude & GPT), each with one
    or more buckets (Weekly / 5h Session). Legacy `fetchAvailableModels`
    fallback only yields one representative row per group — exposed via
    the top-level `percent` / `reset_time` for backward compatibility.
    """

    label: str  # group header, e.g. "Gemini" or "Claude & GPT"
    models: list[str] = []  # member model names displayed in the group header sub-line
    buckets: list[BucketQuota] = []  # preferred; one bar per bucket
    # Legacy single-bar fallback (when only fetchAvailableModels responded):
    percent: Optional[float] = None  # USED 0-100, drives bar when buckets is empty
    reset_time: Optional[str] = None


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
