"""CLI helpers shared by `jobs create` and `q submit` — the compatibility seams."""

from __future__ import annotations

from pathlib import Path

import pytest

from ucloud_api.cli import _default_tag, _parse_mounts
from ucloud_api.exceptions import UCloudError
from ucloud_api.spec import parse_launch_spec

_JOB = {
    "application": {"name": "app", "version": "1"},
    "product": {"id": "p", "category": "c", "provider": "u"},
}


def test_parse_mounts_reads_ro_suffix() -> None:
    parsed = _parse_mounts(["/123/data", "/123/models:ro"])
    assert [(p.path, p.read_only) for p in parsed] == [
        ("/123/data", False),
        ("/123/models", True),
    ]


def test_parse_mounts_rejects_relative_paths() -> None:
    with pytest.raises(UCloudError, match="absolute"):
        _parse_mounts(["data"])


def test_default_tag_is_the_same_for_both_submit_paths() -> None:
    """jobs create and q submit must derive one tag, or logs/exit files split."""
    named = parse_launch_spec({**_JOB, "name": "train-x"})
    assert _default_tag(named, Path("whatever.toml")) == "train-x"
    unnamed = parse_launch_spec(dict(_JOB))
    assert _default_tag(unnamed, Path("specs/train-y.toml")) == "train-y"
