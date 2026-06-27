"""Google OAuth (Antigravity) — retrieveUserQuotaSummary + fetchAvailableModels.

Uses the Antigravity 2.x internal API (same as Antigravity app + `agy` CLI):

  Preferred (Antigravity 2.x groups shape):
    POST {base}/v1internal:retrieveUserQuotaSummary
    → {"groups": [
        {"displayName": "Gemini Models",
         "buckets": [{"bucketId": "weekly", "displayName": "Weekly Limit",
                      "remaining": {"remainingFraction": 0.16},
                      "resetTime": "PT88H45M", "description": "..."}]},
        ...
      ]}

  Fallback (legacy per-model shape):
    POST {base}/v1internal:fetchAvailableModels
    → {"models": {"models/gemini-2.5-pro": {"quotaInfo": {"remainingFraction": 0.16,
                                                            "resetTime": "PT88H45M"}}}}

The summary endpoint exposes the same two groups shown by Antigravity's Model
Quota UI (Gemini / Claude & GPT), each with one or more time-window buckets
(Weekly + 5h Session). The legacy endpoint only yields one representative row
per model with no time-window separation.

An all-100% `fetchAvailableModels` payload is an availability probe, not a
quota summary — we reject it and report `unsupported` so the caller doesn't
display "100% remaining" for quota that's actually unknown.

Token refresh is attempted automatically on 401 if REFRESH_TOKEN is configured.
"""

import logging

import httpx

from ..models import BucketQuota, ChannelConfig, GroupQuota, QuotaResult
from ..token_refresh import refresh_google_token
from .base import QuotaAdapter

log = logging.getLogger(__name__)

# Base URL fallback order matches CLIProxyAPI / agy CLI:
# 1) daily non-sandbox (primary — `agy` CLI / Antigravity app use this for live quota)
# 2) daily sandbox (legacy fallback, kept for compatibility)
# 3) production (last resort; tends to return uninitialized remainingFraction=1)
# See https://github.com/router-for-me/CLIProxyAPI internal/antigravity/executor
# and ANTIGRAVITY_API_SPEC.md (opencode-antigravity-auth).
ANTIGRAVITY_BASE_URLS: list[str] = [
    "https://daily-cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://cloudcode-pa.googleapis.com",
]
SUMMARY_PATH = "v1internal:retrieveUserQuotaSummary"
MODELS_PATH = "v1internal:fetchAvailableModels"
# Hard-coded UA matching the Antigravity CLI.
_DEFAULT_UA = "antigravity/cli/1.0.8 darwin/arm64"


def _short_group_label(name: str) -> str:
    """Normalize Antigravity's `displayName` to one of two stable group labels."""
    n = name.lower()
    if "gemini" in n:
        return "Gemini"
    if "claude" in n or "gpt" in n:
        return "Claude & GPT"
    return name  # unknown — surface as-is


def _short_bucket_label(name: str) -> str:
    """Normalize bucket `displayName` to a stable short label."""
    n = name.lower()
    if "weekly" in n or "week" in n:
        return "Weekly Limit"
    if "5h" in n or "five" in n or "session" in n:
        return "5h Limit"
    return name


def _pretty_model(key: str) -> str:
    """models/gemini-2.5-flash → 'gemini-2.5-flash' (strip models/ prefix)."""
    return key.split("/", 1)[-1] if "/" in key else key


def _first(d: dict, *keys):
    """Return the first non-None value for any of `keys` in `d`.

    cloudcode-pa mixes camelCase (`remainingFraction`, `bucketId`) and snake_case
    (`remaining_fraction`, `bucket_id`) across Antigravity versions; this helper
    lets a single read tolerate both shapes.
    """
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


class GoogleOAuthAdapter(QuotaAdapter):
    async def fetch_quota(self, ch: ChannelConfig, client: httpx.AsyncClient) -> QuotaResult:
        if not ch.api_key:
            return self._err(
                ch,
                "ACCESS_TOKEN not configured (paste from ~/.gemini/ or antigravity settings)",
            )

        headers = self._build_headers(ch)
        payload = {"project": ch.project_id} if ch.project_id else {}

        # 1) Preferred: retrieveUserQuotaSummary (Antigravity 2.x groups shape)
        summary_resp = await self._post(client, ch, headers, SUMMARY_PATH, payload)
        if summary_resp is not None and summary_resp.status_code == 200:
            result = self._parse_summary(ch, summary_resp.json())
            if result is not None:
                return result
            log.info("Antigravity summary endpoint returned no usable quota; falling back")

        # 2) Fallback: fetchAvailableModels (legacy per-model shape)
        models_resp = await self._post(client, ch, headers, MODELS_PATH, payload)
        if models_resp is not None and models_resp.status_code == 200:
            return self._parse_models(ch, models_resp.json())

        return self._err(ch, "no quota data from any endpoint")

    @staticmethod
    def _build_headers(ch: ChannelConfig) -> dict:
        return {
            "Authorization": f"Bearer {ch.api_key}",
            "User-Agent": _DEFAULT_UA,
            "Content-Type": "application/json",
        }

    async def _post(
        self,
        client: httpx.AsyncClient,
        ch: ChannelConfig,
        headers: dict,
        path: str,
        payload: dict,
    ) -> httpx.Response | None:
        """POST against each base URL in order. Returns the first 200 response,
        or None if token refresh failed. On 401, refresh the token once and
        retry that base URL; other non-200 codes fall through to the next base.
        """
        refreshed_this_call = False
        for base in self._base_urls(ch):
            url = f"{base.rstrip('/')}/{path}"
            resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code == 401 and ch.refresh_token and not refreshed_this_call:
                try:
                    new = await refresh_google_token(ch.refresh_token, client)
                    ch.api_key = new["access_token"]
                    headers["Authorization"] = f"Bearer {ch.api_key}"
                    refreshed_this_call = True
                    resp = await client.post(url, json=payload, headers=headers)
                except Exception as exc:
                    log.warning("Antigravity token refresh failed: %s", exc)
                    return None

            if resp.status_code == 200:
                return resp

            log.info(
                "Antigravity %s@%s: status=%d, trying next base URL",
                path,
                base,
                resp.status_code,
            )

        # All bases exhausted — return the last response so caller can decide.
        return resp

    @staticmethod
    def _base_urls(ch: ChannelConfig) -> list[str]:
        """Honour `ch.base_url` override; otherwise use the CLIProxyAPI fallback list."""
        if ch.base_url:
            return [ch.base_url]
        return list(ANTIGRAVITY_BASE_URLS)

    def _parse_summary(self, ch: ChannelConfig, body: dict) -> QuotaResult | None:
        """Parse the Antigravity 2.x summary shape. Returns None if the
        response doesn't have the expected groups/buckets shape."""
        groups_in = body.get("groups")
        if not groups_in:
            log.warning("Antigravity summary: no 'groups' field. body keys=%s", list(body.keys())[:10])
            return None

        groups_out: list[GroupQuota] = []
        overall_used: float | None = None
        skipped_groups: list[str] = []

        for g in groups_in:
            label = _short_group_label(_first(g, "displayName", "display_name") or "")
            buckets_in = g.get("buckets") or _first(g, "bucket_infos") or []
            if not buckets_in:
                skipped_groups.append(f"{label}:no_buckets")
                continue

            buckets_out: list[BucketQuota] = []
            group_used: float | None = None
            skipped_buckets: list[str] = []

            for b in buckets_in:
                # cloudcode-pa returns remainingFraction flat on the bucket (or under
                # `remaining` in older shapes), and may use snake_case. Try flat first,
                # then the nested wrapper, accepting either casing. Note: `0` is a valid
                # remainingFraction (means "no quota left, 100% used") — must NOT short-circuit
                # through `or`, so we explicitly check `is None` between attempts.
                remaining = _first(b, "remainingFraction", "remaining_fraction")
                if remaining is None:
                    remaining = _first(
                        (b.get("remaining") or {}), "remainingFraction", "remaining_fraction"
                    )
                if remaining is None:
                    bid = (
                        _first(b, "bucketId", "bucket_id")
                        or _first(b, "displayName", "display_name")
                        or "?"
                    )
                    skipped_buckets.append(f"{bid}:keys={list(b.keys()) or 'null'}")
                    continue
                frac = float(remaining)
                used = (1.0 - frac) * 100.0
                buckets_out.append(
                    BucketQuota(
                        label=_short_bucket_label(
                            _first(b, "displayName", "display_name") or ""
                        ),
                        percent=round(used, 1),
                        reset_time=_first(b, "resetTime", "reset_time") or None,
                        description=_first(b, "description"),
                    )
                )
                if group_used is None or used > group_used:
                    group_used = used

            if not buckets_out:
                skipped_groups.append(f"{label}:skipped_buckets={skipped_buckets}")
                continue

            tightest = max(buckets_out, key=lambda b: b.percent)
            groups_out.append(
                GroupQuota(
                    label=label,
                    buckets=buckets_out,
                    models=[],
                    percent=tightest.percent,
                    reset_time=tightest.reset_time,
                )
            )
            if overall_used is None or tightest.percent > overall_used:
                overall_used = tightest.percent

        if skipped_groups:
            log.info("Antigravity summary: skipped groups: %s", skipped_groups)

        if not groups_out:
            log.warning("Antigravity summary: no usable quota after parsing. all skipped=%s", skipped_groups)
            return None

        return self._ok(ch, percent=overall_used, groups=groups_out, unit="%")

    def _parse_models(self, ch: ChannelConfig, body: dict) -> QuotaResult:
        """Parse the legacy per-model shape. One representative row per group
        (no time-window separation)."""
        models = body.get("models") or {}
        if not models:
            return self._err(ch, "no models in response")

        groups_out: list[GroupQuota] = []
        overall_used: float | None = None
        any_real_frac = False  # at least one model below 100% remaining

        for label, predicate in [
            ("Gemini", lambda n: "gemini" in n),
            ("Claude & GPT", lambda n: "claude" in n or "gpt" in n),
        ]:
            entries: list[tuple[str, float, str]] = []
            for name, info in models.items():
                if not predicate(name.lower()):
                    continue
                qi = (info or {}).get("quotaInfo") or {}
                frac = qi.get("remainingFraction")
                if frac is None:
                    continue
                f = float(frac)
                if f < 1.0:
                    any_real_frac = True
                entries.append((name, f, qi.get("resetTime") or ""))

            if not entries:
                continue

            # Most conservative = lowest remainingFraction (= highest USED).
            best_name, best_remaining, best_reset = min(entries, key=lambda e: e[1])
            used = round((1.0 - best_remaining) * 100.0, 1)

            member_names = sorted({_pretty_model(n) for n, _, _ in entries})

            groups_out.append(
                GroupQuota(
                    label=label,
                    buckets=[],  # legacy endpoint has no time-window detail
                    models=member_names,
                    percent=used,
                    reset_time=best_reset or None,
                )
            )
            if overall_used is None or used > overall_used:
                overall_used = used

        if not groups_out:
            return self._err(
                ch,
                f"no quotaInfo in any model; keys={list(models.keys())[:5]}",
            )

        # Availability probe: every model 100% → not real quota.
        if not any_real_frac:
            return self._err(
                ch,
                "fetchAvailableModels returned 100% across all models — "
                "likely availability probe, not quota",
            )

        return self._ok(ch, percent=overall_used, groups=groups_out, unit="%")