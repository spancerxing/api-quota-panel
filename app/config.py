"""Env loading + Settings. Loads .env from the project root with a tiny built-in
parser (no python-dotenv dependency) so `uvicorn app.main:app` works locally.
In Docker, .env is injected via env_file and the parser is a no-op (file absent).

`Settings.reload()` re-reads .env into os.environ and rebuilds the channel list
so that runtime token rotations (or manual .env edits) take effect without a
process restart."""

import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from .models import ChannelConfig

# The six channels, in display order.
_CHANNEL_NAMES = ["CODEX", "ANTIGRAVITY", "GEMINI", "GLM", "SILICONFLOW", "PIONEER"]

_PLACEHOLDER_SECRET = "please-generate-a-random-64-char-secret"
_PLACEHOLDER_PASSWORD = "change-me"


def _load_dotenv(path: str = ".env", override: bool = False) -> None:
    """Parse a .env file into os.environ.

    override=False: existing env vars win (initial import — env_file / shell
                    vars take precedence over .env file content).
    override=True:  .env file wins (reload after .env was just rewritten
                    with rotated tokens — file is the new source of truth).
    """
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = val


def _bool(v: Optional[str], default: bool = False) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        self.env_path: Path = Path(os.getenv("PANEL_ENV_FILE", ".env"))
        self.panel_password: str = os.getenv("PANEL_PASSWORD", "")
        self.panel_password_hash: Optional[str] = os.getenv("PANEL_PASSWORD_HASH") or None
        self.panel_session_secret: str = os.getenv("PANEL_SESSION_SECRET", "")
        self.panel_cookie_secure: bool = _bool(os.getenv("PANEL_COOKIE_SECURE"), False)
        self.port: int = int(os.getenv("PORT", "9876"))
        self.cache_ttl: int = int(os.getenv("CACHE_TTL_SECONDS", "120"))
        self.adapter_timeout: float = float(os.getenv("ADAPTER_TIMEOUT_SECONDS", "10"))
        self.channels: list[ChannelConfig] = self._build_channels()

    def _build_channels(self) -> list[ChannelConfig]:
        out: list[ChannelConfig] = []
        for name in _CHANNEL_NAMES:
            out.append(
                ChannelConfig(
                    name=name,
                    enabled=_bool(os.getenv(f"{name}_ENABLED"), True),
                    type=(os.getenv(f"{name}_TYPE") or "unsupported").strip(),
                    api_key=os.getenv(f"{name}_API_KEY") or None,
                    base_url=os.getenv(f"{name}_BASE_URL") or None,
                    label=os.getenv(f"{name}_LABEL") or name.title(),
                    refresh_token=os.getenv(f"{name}_REFRESH_TOKEN") or None,
                    account_id=os.getenv(f"{name}_ACCOUNT_ID") or None,
                    project_id=os.getenv(f"{name}_PROJECT_ID") or None,
                )
            )
        return out

    def enabled_channels(self) -> list[ChannelConfig]:
        return [c for c in self.channels if c.enabled]

    def reload(self) -> None:
        """Re-read .env from disk into os.environ (file wins), then rebuild channels.
        Lets runtime token rotations (or manual .env edits) take effect without
        a process restart. Safe to call between requests."""
        _load_dotenv(str(self.env_path), override=True)
        # refresh scalars (cheap; only re-read on actual rotation in practice)
        self.panel_password = os.getenv("PANEL_PASSWORD", "")
        self.panel_password_hash = os.getenv("PANEL_PASSWORD_HASH") or None
        self.panel_session_secret = os.getenv("PANEL_SESSION_SECRET", "")
        self.panel_cookie_secure = _bool(os.getenv("PANEL_COOKIE_SECURE"), False)
        self.cache_ttl = int(os.getenv("CACHE_TTL_SECONDS", "120"))
        self.adapter_timeout = float(os.getenv("ADAPTER_TIMEOUT_SECONDS", "10"))
        # rebuild channels in place so existing references stay valid
        fresh = self._build_channels()
        self.channels[:] = fresh

    @property
    def using_default_secret(self) -> bool:
        return not self.panel_session_secret or self.panel_session_secret == _PLACEHOLDER_SECRET

    @property
    def using_default_password(self) -> bool:
        return not self.panel_password_hash and (
            not self.panel_password or self.panel_password == _PLACEHOLDER_PASSWORD
        )


def write_env_updates(env_path: Path, updates: dict[str, str]) -> list[str]:
    """Upsert KEY=value lines into an .env file atomically (temp + rename).

    - Replaces any existing line matching ^KEY\\s*= in place (comment lines kept).
    - For new keys (not found in the file): inserts them just BEFORE the next
      section header (`# --- XXX ---`); if no header exists, appends to end.
    - Returns the list of keys actually written.

    Does NOT touch os.environ — caller is responsible for that.
    """
    if not updates:
        return []
    keys = set(updates.keys())

    existing: list[str] = []
    if env_path.is_file():
        existing = env_path.read_text(encoding="utf-8").splitlines()

    out: list[str] = []
    replaced: set[str] = set()
    for ln in existing:
        m = re.match(r"^([A-Z_][A-Z0-9_]*)\s*=", ln.strip())
        if m and m.group(1) in keys and m.group(1) not in replaced:
            out.append(f"{m.group(1)}={updates[m.group(1)]}")
            replaced.add(m.group(1))
        else:
            out.append(ln)

    pending = [k for k in updates if k not in replaced]
    if pending:
        # try to land each new key under its own section header; if none, append.
        section_re = re.compile(r"^#\s*---\s*([A-Z][A-Z0-9_]*)\b", re.IGNORECASE)
        # map: header section name -> insert position (just before next section header)
        section_anchors: dict[str, int] = {}
        for i, ln in enumerate(out):
            m = section_re.match(ln)
            if m:
                section_anchors.setdefault(m.group(1).upper(), i)
        # group pending keys by their inferred section (strip _ENABLED/_TYPE/... to root)
        grouped: dict[str, list[str]] = {}
        order: list[str] = []
        for k in pending:
            # CODEX_API_KEY -> CODEX
            root = k.split("_", 1)[0]
            if root not in grouped:
                grouped[root] = []
                order.append(root)
            grouped[root].append(f"{k}={updates[k]}")

        # insert groups in their header order, then any remaining at end
        next_header_re = re.compile(r"^#\s*---\s*[A-Z]")
        for root in order:
            insert_at: Optional[int] = None
            if root in section_anchors:
                anchor = section_anchors[root]
                insert_at = next(
                    (i for i in range(anchor + 1, len(out)) if next_header_re.match(out[i])),
                    len(out),
                )
            else:
                insert_at = len(out)
            new_block = grouped[root]
            out = out[:insert_at] + new_block + out[insert_at:]
            # shifting indices invalidate later section_anchors; recompute on the fly
            for k_name, v in list(section_anchors.items()):
                if v >= insert_at:
                    section_anchors[k_name] = v + len(new_block)

    env_path.parent.mkdir(parents=True, exist_ok=True)
    # atomic write: temp file in same dir, then os.replace
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=str(env_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
            if out and not out[-1].endswith("\n"):
                f.write("\n")
        os.replace(tmp, env_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return list(updates.keys())


# Load .env then materialize settings at import time.
_load_dotenv()
settings = Settings()
