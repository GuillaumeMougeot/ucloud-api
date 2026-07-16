"""Launch-spec parsing: tool sections, durations, validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from ucloud_api.exceptions import UCloudError
from ucloud_api.spec import (
    SyncSpec,
    load_launch_spec,
    parse_duration_minutes,
    parse_launch_spec,
)

_JOB = {
    "application": {"name": "pytorch-te", "version": "26.05"},
    "product": {"id": "p1", "category": "c1", "provider": "ucloud"},
}


@pytest.mark.parametrize(
    ("value", "minutes"),
    [("4h", 240), ("1h30m", 90), ("90m", 90), ("1H", 60), (45, 45)],
)
def test_parse_duration(value: str | int, minutes: int) -> None:
    assert parse_duration_minutes(value) == minutes


@pytest.mark.parametrize("value", ["", "abc", "4x", "h"])
def test_parse_duration_rejects_garbage(value: str) -> None:
    with pytest.raises(UCloudError):
        parse_duration_minutes(value)


def test_plain_spec_has_no_sections() -> None:
    spec = parse_launch_spec(dict(_JOB))
    assert spec.job.application.name == "pytorch-te"
    assert spec.sync is None and spec.setup is None and spec.schedule is None


def test_tool_sections_are_split_from_the_job() -> None:
    data = {
        **_JOB,
        "sync": {"local": ".", "remote": "/123/repos/proj"},
        "setup": {"python": "uv", "run": "uv run python train.py"},
        "schedule": {"auto_extend": "1h", "max_time": "24h"},
    }
    spec = parse_launch_spec(data)
    assert spec.sync is not None and spec.sync.remote == "/123/repos/proj"
    assert spec.setup is not None and spec.setup.run == "uv run python train.py"
    assert spec.schedule is not None and spec.schedule.auto_extend_minutes == 60
    assert spec.schedule.max_time_minutes == 24 * 60
    # And the sections never leak into the API payload.
    dumped = spec.job.model_dump(by_alias=True, exclude_none=True)
    assert "sync" not in dumped and "setup" not in dumped


def test_setup_requires_sync() -> None:
    with pytest.raises(ValueError, match=r"\[setup\] requires \[sync\]"):
        parse_launch_spec({**_JOB, "setup": {"python": "uv"}})


def test_setup_needs_some_content() -> None:
    with pytest.raises(ValueError, match="at least one of"):
        parse_launch_spec(
            {**_JOB, "sync": {"remote": "/1/x"}, "setup": {}},
        )


def test_sync_in_job_path_is_work_basename() -> None:
    assert SyncSpec(remote="/123/repos/proj").in_job_path == "/work/proj"
    assert SyncSpec(remote="/123/data/").in_job_path == "/work/data"


def test_load_launch_spec_sets_base_dir(tmp_path: Path) -> None:
    spec_file = tmp_path / "job.toml"
    spec_file.write_text(
        'name = "t"\n'
        "[application]\nname = 'a'\nversion = '1'\n"
        "[product]\nid = 'p'\ncategory = 'c'\nprovider = 'u'\n",
        encoding="utf-8",
    )
    spec = load_launch_spec(spec_file)
    assert spec.base_dir == tmp_path.resolve()
    assert spec.job.name == "t"
