"""Aggregate quota fetch across all enabled channels in parallel, with per-channel
timeout and error isolation — one failing/hung channel never breaks the others."""

import asyncio

import httpx

from .adapters import get_adapter
from .models import ChannelConfig, QuotaResult


def _make_client(timeout: float) -> httpx.AsyncClient:
    """Build an httpx client that ignores proxy env vars (direct connection)."""
    transport = httpx.AsyncHTTPTransport(proxy=None)
    return httpx.AsyncClient(transport=transport, timeout=timeout)


async def _safe_fetch(ch: ChannelConfig, client: httpx.AsyncClient, timeout: float) -> QuotaResult:
    adapter = get_adapter(ch.type)
    try:
        return await asyncio.wait_for(adapter.fetch_quota(ch, client), timeout=timeout)
    except asyncio.TimeoutError:
        return QuotaResult(
            id=ch.name,
            label=ch.label,
            type=ch.type,
            supported=(ch.type != "unsupported"),
            status="error",
            error=f"timeout after {timeout}s",
        )
    except Exception as e:  # noqa: BLE001 — isolation: convert any error to a card status
        return QuotaResult(
            id=ch.name,
            label=ch.label,
            type=ch.type,
            supported=(ch.type != "unsupported"),
            status="error",
            error=f"{type(e).__name__}: {e}",
        )


async def fetch_all(channels: list[ChannelConfig], timeout: float) -> list[QuotaResult]:
    """Run every channel's adapter concurrently. Order of results matches input order."""
    if not channels:
        return []
    _client = _make_client(timeout)

    async with _client as client:
        tasks = [_safe_fetch(ch, client, timeout) for ch in channels]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[QuotaResult] = []
    for ch, r in zip(channels, gathered):
        if isinstance(r, QuotaResult):
            out.append(r)
        else:  # belt-and-suspenders: _safe_fetch already converts, but guard anyway
            out.append(
                QuotaResult(
                    id=ch.name,
                    label=ch.label,
                    type=ch.type,
                    supported=(ch.type != "unsupported"),
                    status="error",
                    error=f"{type(r).__name__}: {r}",
                )
            )
    return out


async def fetch_one(ch: ChannelConfig, timeout: float) -> QuotaResult:
    """Run a single channel's adapter with the same isolation guarantees as fetch_all."""
    _client = _make_client(timeout)
    async with _client as client:
        return await _safe_fetch(ch, client, timeout)
