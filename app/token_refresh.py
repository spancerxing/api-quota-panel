"""OAuth token refresh helpers for codex-web and google-oauth adapters.

google-oauth uses the Antigravity OAuth client (hardcoded in the desktop app
as a public OAuth app credential)"""

import os
import httpx

# --- Codex (OpenAI ChatGPT OAuth) ---
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"

# --- Google OAuth (Antigravity) ---
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

async def refresh_codex_token(
    refresh_token: str, client: httpx.AsyncClient
) -> dict:
    """Exchange a codex refresh_token for a new access_token (and possibly new refresh_token).

    POST to https://auth.openai.com/oauth/token with grant_type=refresh_token.

    Returns dict with keys: access_token, refresh_token (may be a new one).
    """
    data = {
        "client_id": os.getenv("CODEX_CLIENT_ID", ''),
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": "openid email profile offline_access",
    }
    resp = await client.post(
        CODEX_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    result = resp.json()
    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token", refresh_token),
    }


async def refresh_google_token(
    refresh_token: str, client: httpx.AsyncClient
) -> dict:
    """Exchange an Antigravity Google OAuth refresh_token for a new access_token.

    POST to https://oauth2.googleapis.com/token with grant_type=refresh_token.

    Returns dict with keys: access_token, refresh_token (may be a new one).
    """
    data = {
        "client_id": os.getenv("GOOGLE_CLIENT_ID", ''),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET", ''),
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    resp = await client.post(
        GOOGLE_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    result = resp.json()
    return {
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token", refresh_token),
    }