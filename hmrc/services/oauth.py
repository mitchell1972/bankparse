"""
HMRC OAuth 2.0 authorisation-code flow.

Reference: https://developer.service.hmrc.gov.uk/api-documentation/docs/authorisation/user-restricted-endpoints

Flow:
    1. /api/hmrc/connect generates an `authorize` URL with the right scopes
       and a CSRF-bound `state` value, redirects the user.
    2. HMRC bounces back to `/api/hmrc/callback?code=...&state=...`.
    3. Server exchanges the code for an access + refresh token at /oauth/token.
    4. Tokens stored AES-GCM encrypted in `hmrc_connections`.

Access tokens expire in 4 hours. Refresh tokens live 18 months. The client
wrapper (services/client.py) handles automatic refresh on 401.

Scopes required for MTD ITSA (per HMRC API docs):
    read:self-assessment     for obligations, business details, calculations
    write:self-assessment    for quarterly submissions, EOPS, final declaration
"""

from __future__ import annotations

import logging
import secrets
import urllib.parse as _urlparse

from .. import config as _cfg

logger = logging.getLogger("bankparse.hmrc.oauth")


REQUIRED_SCOPES = ["read:self-assessment", "write:self-assessment"]


def build_authorize_url(
    state: str,
    redirect_uri: str | None = None,
    *,
    prompt_login: bool = False,
) -> str:
    """Build the URL we redirect the user to in order to grant access.

    `state` is an unguessable token we generated and bound to the user's
    server-side session; we verify it matches in the callback to prevent CSRF.

    `prompt_login=True` adds `prompt=login` so HMRC's sandbox tears down any
    existing Government Gateway session cookie and re-prompts for credentials.
    Without this, a developer who's just minted a fresh sandbox test user
    silently OAuths back in as the *previous* sandbox identity — producing
    a token whose `nino` doesn't match the freshly-minted NINO. HMRC then
    rejects subsequent `create-test-business` calls with `MATCHING_RESOURCE_
    NOT_FOUND`, surfaced in our UI as `OAUTH_NINO_MISMATCH`.

    `prompt=login` is OIDC standard (RFC 6749 §3.1.2.1 extension), not pure
    OAuth 2.0. HMRC's OAuth endpoint is RFC 6749 and may ignore it — but
    sending it costs nothing and has been observed to work on
    test-api.service.hmrc.gov.uk. The defence in depth is the UX path: when
    the token's identity does mismatch, we surface a one-click recovery
    card with the GG creds inline so the user can sign in correctly.
    """
    params = {
        "response_type": "code",
        "client_id": _cfg.HMRC_CLIENT_ID,
        "scope": " ".join(REQUIRED_SCOPES),
        "redirect_uri": redirect_uri or _cfg.HMRC_REDIRECT_URI,
        "state": state,
    }
    if prompt_login:
        params["prompt"] = "login"
    return f"{_cfg.HMRC_BASE_URL}{_cfg.OAUTH_AUTHORIZE_PATH}?{_urlparse.urlencode(params)}"


def new_state() -> str:
    """Generate a fresh anti-CSRF state token."""
    return secrets.token_urlsafe(32)


def exchange_code_for_tokens(code: str, redirect_uri: str | None = None) -> dict:
    """POST /oauth/token to exchange the auth code for tokens.

    Returns the raw HMRC token response:
        {
          "access_token":  "...",
          "refresh_token": "...",
          "expires_in":    14400,
          "scope":         "read:self-assessment write:self-assessment",
          "token_type":    "bearer"
        }

    Caller is responsible for encrypting and persisting these immediately.
    """
    import httpx

    payload = {
        "grant_type": "authorization_code",
        "client_id": _cfg.HMRC_CLIENT_ID,
        "client_secret": _cfg.HMRC_CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri or _cfg.HMRC_REDIRECT_URI,
    }
    url = f"{_cfg.HMRC_BASE_URL}{_cfg.OAUTH_TOKEN_PATH}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, data=payload)
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token: str) -> dict:
    """Use a refresh token to get a new access token.

    HMRC ROTATES the refresh token on each use — store the new one returned
    in the response. If you keep using the old one you'll lose the connection.
    """
    import httpx

    payload = {
        "grant_type": "refresh_token",
        "client_id": _cfg.HMRC_CLIENT_ID,
        "client_secret": _cfg.HMRC_CLIENT_SECRET,
        "refresh_token": refresh_token,
    }
    url = f"{_cfg.HMRC_BASE_URL}{_cfg.OAUTH_TOKEN_PATH}"
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, data=payload)
    resp.raise_for_status()
    return resp.json()
