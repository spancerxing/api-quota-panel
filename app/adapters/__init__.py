"""Adapter registry: TYPE → adapter class. Unknown types fall back to Unsupported."""

from .base import QuotaAdapter
from .codex_web import CodexWebAdapter
from .google_oauth import GoogleOAuthAdapter
from .openai_relay import OpenAIRelayAdapter
from .pioneer import PioneerAdapter
from .siliconflow import SiliconFlowAdapter
from .unsupported import UnsupportedAdapter

REGISTRY = {
    "siliconflow": SiliconFlowAdapter,
    "openai-relay": OpenAIRelayAdapter,
    "google-oauth": GoogleOAuthAdapter,
    "codex-web": CodexWebAdapter,
    "pioneer": PioneerAdapter,
    "unsupported": UnsupportedAdapter,
}


def get_adapter(type_name: str) -> QuotaAdapter:
    return REGISTRY.get(type_name, UnsupportedAdapter)()


__all__ = ["QuotaAdapter", "get_adapter", "REGISTRY"]
