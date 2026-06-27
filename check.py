"""CLI to test one or all adapters before wiring the UI.

Usage:
  python check.py            # list channels + status
  python check.py all        # fetch all enabled channels in parallel
  python check.py SILICONFLOW   # fetch one channel (name from .env)
  python check.py GLM

Loads .env (via app.config). Prints each QuotaResult as JSON."""

import asyncio
import json
import sys

import httpx

from app.adapters import get_adapter
from app.aggregator import fetch_all
from app.config import settings


def _make_client(timeout: float) -> httpx.AsyncClient:
    transport = httpx.AsyncHTTPTransport(proxy=None)
    return httpx.AsyncClient(transport=transport, timeout=timeout)


def _print_channels() -> None:
    print("channels (from .env):")
    for c in settings.channels:
        flag = "ON " if c.enabled else "off"
        key = "key" if c.api_key else "NO KEY"
        url = c.base_url or "-"
        print(f"  [{flag}] {c.name:<12} type={c.type:<13} {key}  base={url}  label={c.label}")


async def run_one(name: str) -> int:
    name = name.upper()
    ch = next((c for c in settings.channels if c.name == name), None)
    if ch is None:
        print(f"unknown channel: {name}")
        _print_channels()
        return 1
    print(f"== {ch.name} ({ch.label}) type={ch.type} ==")
    async with _make_client(settings.adapter_timeout) as client:
        adapter = get_adapter(ch.type)
        result = await adapter.fetch_quota(ch, client)
    print(result.model_dump_json(indent=2))
    return 0 if result.status == "ok" else 0  # always 0 — we want to see errors, not exit-fail


async def run_all() -> int:
    channels = settings.enabled_channels()
    if not channels:
        print("no enabled channels")
        return 1
    print(f"fetching {len(channels)} channel(s) in parallel (timeout={settings.adapter_timeout}s)...")
    results = await fetch_all(channels, settings.adapter_timeout)
    print(json.dumps([r.model_dump(mode="json") for r in results], indent=2, ensure_ascii=False))
    print("\nsummary:")
    for r in results:
        metric = (
            f"{r.balance}{r.unit}" if r.balance is not None and r.unit != "%"
            else f"{r.percent}%" if r.percent is not None else "-"
        )
        print(f"  {r.label:<16} {r.status:<12} {metric}")
    return 0


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _print_channels()
        print("\nusage: python check.py [CHANNEL_NAME|all]")
        sys.exit(0)
    arg = sys.argv[1]
    rc = asyncio.run(run_all() if arg.lower() == "all" else run_one(arg))
    sys.exit(rc)


if __name__ == "__main__":
    main()
