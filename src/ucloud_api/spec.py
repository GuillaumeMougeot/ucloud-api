"""Extended job spec files: the UCloud ``JobSpecification`` plus tool sections.

A spec TOML is UCloud's ``JobSpecification`` 1:1, with three optional
tool-level sections that never reach the API:

* ``[sync]`` — push a local working tree to a drive folder and mount it.
* ``[setup]`` — prepare the machine (env install and/or a command to run).
* ``[schedule]`` — queue policy: auto-extend step and total time cap.

``load_launch_spec`` pops these sections and validates the rest as a
``JobSpecification``, so plain job specs keep working unchanged.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from .exceptions import UCloudError
from .models import JobSpecification

_TOOL_SECTIONS = ("sync", "setup", "schedule")

_DURATION_RE = re.compile(r"^\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*$", re.IGNORECASE)


def parse_duration_minutes(value: str | int) -> int:
    """Parse ``"4h"``, ``"1h30m"``, ``"90m"`` or a plain minute count into minutes."""
    if isinstance(value, int):
        return value
    match = _DURATION_RE.match(value)
    if not match or (match.group(1) is None and match.group(2) is None):
        raise UCloudError(f"Invalid duration {value!r} (use e.g. '4h', '1h30m' or '90m').")
    hours, minutes = int(match.group(1) or 0), int(match.group(2) or 0)
    return hours * 60 + minutes


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SyncSpec(_Section):
    """``[sync]``: push ``local`` to ``remote`` on a drive, and mount it."""

    local: str = "."
    remote: str
    mount: bool = True

    @property
    def in_job_path(self) -> str:
        """Where the synced folder appears inside the job (mounts land in /work)."""
        return "/work/" + self.remote.rstrip("/").rsplit("/", 1)[-1]


class SetupSpec(_Section):
    """``[setup]``: prepare the environment and optionally run a command.

    * ``python = "uv"`` — install uv and ``uv sync`` the synced repo.
    * ``script`` — a local shell script embedded into the generated one.
    * ``run`` — a command executed in the synced repo. When set, the job runs
      as a **batch** job: UCloud terminates it when the command exits, and its
      exit code is recorded for the queue's dependency checks.
    * ``param`` — override the application parameter to wire the script to
      (defaults to ``batchScript`` when ``run`` is set, else ``initScript``).
    """

    python: str | None = None
    script: str | None = None
    run: str | None = None
    param: str | None = None

    @model_validator(mode="after")
    def _validate(self) -> SetupSpec:
        if not (self.python or self.script or self.run):
            raise ValueError("[setup] needs at least one of: python, script, run")
        if self.python is not None and self.python != "uv":
            raise ValueError(f"[setup] python={self.python!r} is not supported (only 'uv')")
        return self


class ScheduleSpec(_Section):
    """``[schedule]``: queue policy for `ucloud q` jobs."""

    auto_extend: str | None = None  # e.g. "1h": extend by this when time runs low
    max_time: str | None = None  # e.g. "24h": never extend past this total

    @model_validator(mode="after")
    def _validate(self) -> ScheduleSpec:
        # Parse eagerly so a bad duration fails at submit, not mid-run.
        if self.auto_extend is not None:
            parse_duration_minutes(self.auto_extend)
        if self.max_time is not None:
            parse_duration_minutes(self.max_time)
        return self

    @property
    def auto_extend_minutes(self) -> int | None:
        return None if self.auto_extend is None else parse_duration_minutes(self.auto_extend)

    @property
    def max_time_minutes(self) -> int | None:
        return None if self.max_time is None else parse_duration_minutes(self.max_time)


class LaunchSpec(BaseModel):
    """A job specification plus the tool sections that drive sync/setup/queueing."""

    job: JobSpecification
    sync: SyncSpec | None = None
    setup: SetupSpec | None = None
    schedule: ScheduleSpec | None = None
    #: Directory the spec file lives in; relative [setup] script paths resolve here.
    base_dir: Path = Path()

    @model_validator(mode="after")
    def _validate(self) -> LaunchSpec:
        if self.setup is not None and self.sync is None:
            raise ValueError(
                "[setup] requires [sync]: the generated script is stored in, and runs "
                "from, the synced folder"
            )
        return self


def parse_launch_spec(data: dict[str, Any], *, base_dir: Path = Path()) -> LaunchSpec:
    """Build a :class:`LaunchSpec` from a parsed spec-TOML dict."""
    payload = dict(data)
    sections = {name: payload.pop(name, None) for name in _TOOL_SECTIONS}
    return LaunchSpec(
        job=JobSpecification.model_validate(payload),
        sync=None if sections["sync"] is None else SyncSpec.model_validate(sections["sync"]),
        setup=None if sections["setup"] is None else SetupSpec.model_validate(sections["setup"]),
        schedule=(
            None
            if sections["schedule"] is None
            else ScheduleSpec.model_validate(sections["schedule"])
        ),
        base_dir=base_dir,
    )


def load_launch_spec(path: Path) -> LaunchSpec:
    """Load and validate a spec TOML file."""
    if not path.exists():
        raise UCloudError(f"Spec file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise UCloudError(f"Could not parse {path}: {exc}") from exc
    try:
        return parse_launch_spec(data, base_dir=path.resolve().parent)
    except ValueError as exc:
        raise UCloudError(f"Invalid job specification in {path}: {exc}") from exc
