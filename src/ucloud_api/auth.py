"""Turn a long-lived refresh token into short-lived access tokens.

UCloud's web frontend refreshes via ``POST /auth/refresh/web`` using an httpOnly
cookie plus an ``X-CSRFToken`` header. For programmatic clients the equivalent is
``POST /auth/refresh`` with the refresh token sent as a bearer token; the response
is ``{"accessToken": "<jwt>", "csrfToken": "..."}``. The access token is a JWT
whose ``exp`` claim tells us exactly when to refresh again, so we cache it and only
hit the network when it is about to expire.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from pathlib import Path
from typing import cast

import httpx

from .config import token_cache_path
from .exceptions import AuthError

# Refresh a little before the real expiry to absorb clock skew and slow requests.
_EXPIRY_SKEW_SECONDS = 60
# Fallback lifetime if a token has no decodable ``exp`` claim.
_DEFAULT_LIFETIME_SECONDS = 9 * 60


def _decode_jwt_exp(token: str) -> int | None:
    """Return the ``exp`` (unix seconds) claim of a JWT without verifying it.

    We only read the expiry to schedule refreshes; we never trust the token's
    contents for authorization, so signature verification is unnecessary here.
    """
    try:
        payload_segment = token.split(".")[1]
    except IndexError:
        return None
    padding = "=" * (-len(payload_segment) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload_segment + padding)
        claims = json.loads(raw)
    except (binascii.Error, ValueError):
        return None
    exp = claims.get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


class Authenticator:
    """Mints and caches UCloud access tokens from a refresh token."""

    def __init__(
        self,
        refresh_token: str,
        base_url: str,
        *,
        http: httpx.Client | None = None,
        cache_path: Path | None = None,
    ) -> None:
        self._refresh_token = refresh_token
        self._base_url = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=30.0)
        self._owns_http = http is None
        self._cache_path = cache_path if cache_path is not None else token_cache_path()
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self._load_cache()

    def access_token(self, *, force: bool = False) -> str:
        """Return a valid access token, refreshing it if necessary."""
        if not force and self._access_token and time.time() < self._expires_at:
            return self._access_token
        return self._refresh()

    def _refresh(self) -> str:
        url = f"{self._base_url}/auth/refresh"
        try:
            resp = self._http.post(url, headers={"Authorization": f"Bearer {self._refresh_token}"})
        except httpx.HTTPError as exc:
            raise AuthError(f"Could not reach UCloud auth endpoint {url}: {exc}") from exc

        if resp.status_code in (401, 403):
            raise AuthError(
                "UCloud rejected the refresh token (expired or invalid). "
                "Log in again in the browser and run `ucloud login` with a fresh token."
            )
        if resp.status_code >= 400:
            raise AuthError(f"Token refresh failed ({resp.status_code}): {resp.text[:200]}")

        try:
            access_token = cast(str, resp.json()["accessToken"])
        except (ValueError, KeyError, TypeError) as exc:
            raise AuthError(f"Unexpected refresh response: {resp.text[:200]}") from exc

        exp = _decode_jwt_exp(access_token)
        self._access_token = access_token
        self._expires_at = (
            exp - _EXPIRY_SKEW_SECONDS
            if exp is not None
            else time.time() + _DEFAULT_LIFETIME_SECONDS
        )
        self._save_cache()
        return access_token

    def _load_cache(self) -> None:
        if not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        token = data.get("access_token")
        expires_at = data.get("expires_at", 0)
        if isinstance(token, str) and time.time() < float(expires_at):
            self._access_token = token
            self._expires_at = float(expires_at)

    def _save_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps({"access_token": self._access_token, "expires_at": self._expires_at}),
                encoding="utf-8",
            )
            self._cache_path.chmod(0o600)
        except OSError:
            # A missing cache is a performance concern, not a correctness one.
            pass

    def close(self) -> None:
        if self._owns_http:
            self._http.close()
