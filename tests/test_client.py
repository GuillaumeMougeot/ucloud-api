"""Error reporting for the HTTP client."""

from __future__ import annotations

import httpx

from ucloud_api.client import _why


def test_why_extracts_ucloud_explanation() -> None:
    resp = httpx.Response(
        400, json={"statusCode": 400, "why": "This application does not support SSH"}
    )
    assert _why(resp) == ": This application does not support SSH"


def test_why_ignores_empty_and_missing() -> None:
    assert _why(httpx.Response(400, json={"why": ""})) == ""
    assert _why(httpx.Response(400, json={"statusCode": 400})) == ""


def test_why_survives_non_json_body() -> None:
    assert _why(httpx.Response(502, text="<html>bad gateway</html>")) == ""


def test_why_survives_json_that_is_not_an_object() -> None:
    assert _why(httpx.Response(400, json=["nope"])) == ""
