"""Tiny in-memory TTL cache for quota results. /api/quota returns cached; ?refresh=1
forces a parallel re-fetch. Stale entries are still returned as a last resort so the
UI always has something to show during a slow refresh."""

import time

from .models import QuotaResult


class TTLCache:
    def __init__(self, ttl: int):
        self.ttl = ttl
        self._store: dict[str, tuple[QuotaResult, float]] = {}

    def set_all(self, results: list[QuotaResult]) -> None:
        now = time.time()
        self._store = {r.id: (r, now) for r in results}

    def set_one(self, result: QuotaResult) -> None:
        """Replace a single channel's cached entry without touching the others."""
        self._store[result.id] = (result, time.time())

    def invalidate(self, channel_id: str) -> None:
        """Drop a single channel's cached entry (next read re-fetches only this one)."""
        self._store.pop(channel_id, None)

    def is_stale(self, channel_id: str) -> bool:
        """True if this channel is missing from cache or older than TTL."""
        entry = self._store.get(channel_id)
        if entry is None:
            return True
        return time.time() - entry[1] > self.ttl

    def all(self) -> list[QuotaResult]:
        if not self._store:
            return []
        now = time.time()
        fresh = [r for r, ts in self._store.values() if now - ts < self.ttl]
        return fresh if fresh else [r for r, _ in self._store.values()]

    def expired(self) -> bool:
        if not self._store:
            return True
        now = time.time()
        return any(now - ts > self.ttl for _, ts in self._store.values())
