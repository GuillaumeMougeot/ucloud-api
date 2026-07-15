"""Token refresh and JWT expiry handling."""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest
import respx

from ucloud_api.auth import Authenticator, _decode_jwt_exp
from ucloud_api.exceptions import AuthError

BASE = "https://cloud.example.dk"


def _make_jwt(exp: int) -> str:
    def seg(obj: dict[str, object]) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{seg({'alg': 'RS256'})}.{seg({'exp': exp, 'sub': 'me'})}.signature"


def test_decode_jwt_exp_reads_claim() -> None:
    token = _make_jwt(1_700_000_000)
    assert _decode_jwt_exp(token) == 1_700_000_000


def test_decode_jwt_exp_handles_garbage() -> None:
    assert _decode_jwt_exp("not-a-jwt") is None


@respx.mock
def test_refresh_uses_bearer_and_caches(tmp_path) -> None:
    access = _make_jwt(int(time.time()) + 600)
    route = respx.post(f"{BASE}/auth/refresh").mock(
        return_value=httpx.Response(200, json={"accessToken": access, "csrfToken": "x"})
    )
    auth = Authenticator("refresh-tok", BASE, cache_path=tmp_path / "cache.json")

    assert auth.access_token() == access
    # Second call is served from cache; no extra network request.
    assert auth.access_token() == access
    assert route.call_count == 1

    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer refresh-tok"


@respx.mock
def test_refresh_rejects_bad_token(tmp_path) -> None:
    respx.post(f"{BASE}/auth/refresh").mock(return_value=httpx.Response(401))
    auth = Authenticator("bad", BASE, cache_path=tmp_path / "cache.json")
    with pytest.raises(AuthError):
        auth.access_token()


@respx.mock
def test_expired_cache_triggers_new_refresh(tmp_path) -> None:
    expired = _make_jwt(int(time.time()) - 10)
    fresh = _make_jwt(int(time.time()) + 600)
    respx.post(f"{BASE}/auth/refresh").mock(
        side_effect=[
            httpx.Response(200, json={"accessToken": expired}),
            httpx.Response(200, json={"accessToken": fresh}),
        ]
    )
    auth = Authenticator("tok", BASE, cache_path=tmp_path / "cache.json")
    first = auth.access_token()
    # First token is already past expiry (minus skew), so a second call refreshes.
    second = auth.access_token()
    assert first == expired
    assert second == fresh
