"""Presentation helpers in the CLI."""

from __future__ import annotations

import time

import pytest

from ucloud_api.cli import _format_timestamp

# 2026-07-16T15:14:00Z — inside CEST (UTC+2), so a UTC render is off by two hours.
_EPOCH_MS = 1784214840000


@pytest.fixture
def copenhagen(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TZ", "Europe/Copenhagen")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_format_timestamp_renders_local_not_utc(copenhagen: None) -> None:
    assert _format_timestamp(_EPOCH_MS) == "2026-07-16 17:14"


def test_format_timestamp_follows_the_machine_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        assert _format_timestamp(_EPOCH_MS) == "2026-07-16 15:14"
    finally:
        monkeypatch.undo()
        time.tzset()


def test_format_timestamp_ignores_non_numeric() -> None:
    assert _format_timestamp(None) == ""
    assert _format_timestamp("nope") == ""
