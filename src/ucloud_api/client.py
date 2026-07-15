"""Thin authenticated HTTP client over UCloud's JSON API."""

from __future__ import annotations

from typing import Any

import httpx

from .auth import Authenticator
from .config import Credentials, load_credentials
from .exceptions import APIError


class UCloudClient:
    """Authenticated wrapper around ``httpx`` that talks to UCloud.

    Access tokens expire quickly, so every request injects a freshly minted one
    and transparently retries once on ``401`` in case the token expired in-flight.
    """

    def __init__(
        self,
        credentials: Credentials | None = None,
        *,
        timeout: float = 60.0,
    ) -> None:
        self._creds = credentials or load_credentials()
        self._http = httpx.Client(base_url=self._creds.base_url, timeout=timeout)
        self._auth = Authenticator(self._creds.refresh_token, self._creds.base_url, http=self._http)

    # -- lifecycle ---------------------------------------------------------- #

    def __enter__(self) -> UCloudClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- request plumbing --------------------------------------------------- #

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        resp = self._send(method, path, params=params, json=json, force_refresh=False)
        if resp.status_code == 401:
            # Token may have expired between mint and use; refresh once and retry.
            resp = self._send(method, path, params=params, json=json, force_refresh=True)
        if resp.status_code >= 400:
            raise APIError(
                f"{method} {path} failed with {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )
        if not resp.content:
            return None
        return resp.json()

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        json: Any | None,
        force_refresh: bool,
    ) -> httpx.Response:
        token = self._auth.access_token(force=force_refresh)
        headers = {"Authorization": f"Bearer {token}"}
        # Drop None-valued query params so callers can pass optionals freely.
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            return self._http.request(
                method, path, params=clean_params or None, json=json, headers=headers
            )
        except httpx.HTTPError as exc:
            raise APIError(f"{method} {path} failed: {exc}") from exc

    # Convenience verbs -----------------------------------------------------

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, *, json: Any | None = None) -> Any:
        return self.request("POST", path, json=json)
