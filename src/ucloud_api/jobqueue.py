"""A local job queue with dependencies, quota gating, and time policies.

There is no server-side queue on UCloud, so this one lives in files under the
user data directory — one JSON record per queued job — and is advanced by
**ticks** (``ucloud q tick``, or ``ucloud q daemon`` looping it). Design rule:
the queue holds *state, not authority*. Every tick reconciles against the live
jobs API, so killing the daemon never harms running jobs — they are ordinary
UCloud jobs you can watch from the web GUI — and queued specs simply wait on
disk until something ticks again.

A tick:

1. refreshes records of submitted jobs from the API, recording success or
   failure (batch runs report the run command's exit code);
2. extends jobs that are running low on time, when ``[schedule] auto_extend``
   allows and ``max_time`` is not exceeded;
3. submits queued records whose dependencies are all ``DONE`` and whose
   product category still has quota, running the full launch pipeline
   (sync + setup) at submission time — dependents run *current* code.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from platformdirs import user_data_dir

from .catalog import Catalog
from .client import UCloudClient
from .config import APP_NAME
from .exceptions import UCloudError
from .jobs import Jobs
from .launch import Launcher
from .models import JobState
from .spec import LaunchSpec, parse_launch_spec

#: Extend when a monitored job has less than this many minutes left.
EXTEND_THRESHOLD_MINUTES = 15
#: Cap for auto-extension when [schedule] max_time is not given.
DEFAULT_MAX_TIME_MINUTES = 24 * 60


def queue_dir() -> Path:
    """The directory holding one JSON record per queued job."""
    override = os.environ.get("UCLOUD_QUEUE_DIR")
    base = Path(override) if override else Path(user_data_dir(APP_NAME)) / "queue"
    base.mkdir(parents=True, exist_ok=True)
    return base


class QueueStatus(StrEnum):
    QUEUED = "QUEUED"  # waiting for dependencies/quota
    SUBMITTED = "SUBMITTED"  # created on UCloud, not yet running
    RUNNING = "RUNNING"
    DONE = "DONE"  # finished successfully (exit code 0 for batch runs)
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"  # a dependency failed; will never run
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in {
            QueueStatus.DONE,
            QueueStatus.FAILED,
            QueueStatus.BLOCKED,
            QueueStatus.CANCELLED,
        }


@dataclass(slots=True)
class QueueRecord:
    """One queued job: the spec snapshot plus lifecycle state."""

    name: str
    spec: dict[str, Any]  # raw spec-TOML dict, snapshotted at `q submit`
    base_dir: str  # where relative [sync]/[setup] paths resolve
    after: list[str] = field(default_factory=list)
    status: QueueStatus = QueueStatus.QUEUED
    job_id: str | None = None
    message: str = ""
    created_at: float = field(default_factory=time.time)
    submitted_at: float | None = None
    finished_at: float | None = None
    extended_minutes: int = 0

    def launch_spec(self) -> LaunchSpec:
        return parse_launch_spec(self.spec, base_dir=Path(self.base_dir))


class Queue:
    """File-backed queue storage."""

    def __init__(self, directory: Path | None = None) -> None:
        self.dir = directory or queue_dir()

    def _path(self, name: str) -> Path:
        return self.dir / f"{name}.json"

    def get(self, name: str) -> QueueRecord | None:
        path = self._path(name)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        data["status"] = QueueStatus(data["status"])
        return QueueRecord(**data)

    def save(self, record: QueueRecord) -> None:
        payload = asdict(record)
        payload["status"] = record.status.value
        self._path(record.name).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)

    def all(self) -> list[QueueRecord]:
        records = [self.get(p.stem) for p in sorted(self.dir.glob("*.json"))]
        return sorted((r for r in records if r is not None), key=lambda r: r.created_at)

    def add(self, record: QueueRecord) -> None:
        if self.get(record.name) is not None:
            raise UCloudError(
                f"A queued job named {record.name!r} already exists "
                "(pick another --name or `ucloud q rm` it)."
            )
        for dep in record.after:
            if self.get(dep) is None:
                raise UCloudError(f"--after {dep!r}: no queued job with that name.")
        self.save(record)


class Scheduler:
    """Advances the queue one tick at a time. Stateless between ticks."""

    def __init__(self, client: UCloudClient, queue: Queue | None = None) -> None:
        self._client = client
        self._queue = queue or Queue()
        self._jobs = Jobs(client)
        self._launcher = Launcher(client)
        self._usable_categories: set[tuple[str, str]] | None = None  # per-tick cache

    def tick(self) -> list[str]:
        """Reconcile + extend + submit. Returns human-readable event lines."""
        self._usable_categories = None
        events: list[str] = []
        records = self._queue.all()
        by_name = {r.name: r for r in records}

        for record in records:
            if record.status in (QueueStatus.SUBMITTED, QueueStatus.RUNNING):
                events += self._refresh(record)
        for record in records:
            if record.status is QueueStatus.QUEUED:
                events += self._maybe_submit(record, by_name)
        return events

    # -- reconcile a submitted/running record -------------------------------- #

    def _refresh(self, record: QueueRecord) -> list[str]:
        assert record.job_id is not None
        job = self._jobs.retrieve(record.job_id)
        status = job.get("status", {})
        state = JobState(str(status.get("state", "FAILURE")))

        if state is JobState.RUNNING:
            events: list[str] = []
            if record.status is not QueueStatus.RUNNING:
                record.status = QueueStatus.RUNNING
                events.append(f"{record.name}: job {record.job_id} is RUNNING")
            events += self._maybe_extend(record, status)
            self._queue.save(record)
            return events

        if state.is_terminal:
            exit_code = self._launcher.read_exit_code(record.launch_spec(), record.name)
            ok = state is JobState.SUCCESS and (exit_code is None or exit_code == 0)
            record.status = QueueStatus.DONE if ok else QueueStatus.FAILED
            record.finished_at = time.time()
            record.message = f"job state {state.value}" + (
                f", run exit code {exit_code}" if exit_code is not None else ""
            )
            self._queue.save(record)
            return [f"{record.name}: {record.status.value} ({record.message})"]

        return []  # IN_QUEUE / CANCELING / SUSPENDED: nothing to do yet

    def _maybe_extend(self, record: QueueRecord, status: dict[str, Any]) -> list[str]:
        spec = record.launch_spec()
        step = spec.schedule.auto_extend_minutes if spec.schedule else None
        if step is None:
            return []
        started, expires = status.get("startedAt"), status.get("expiresAt")
        if not isinstance(started, (int, float)) or not isinstance(expires, (int, float)):
            return []
        now_ms = time.time() * 1000
        if (expires - now_ms) / 60_000 > EXTEND_THRESHOLD_MINUTES:
            return []
        cap = (
            spec.schedule.max_time_minutes if spec.schedule else None
        ) or DEFAULT_MAX_TIME_MINUTES
        allocated = (expires - started) / 60_000
        if allocated + step > cap:
            return [
                f"{record.name}: not extending past max_time "
                f"({allocated:.0f}m allocated, cap {cap}m)"
            ]
        self._jobs.extend(record.job_id or "", hours=step // 60, minutes=step % 60)
        record.extended_minutes += step
        self._queue.save(record)
        return [f"{record.name}: extended job {record.job_id} by {step}m (low on time)"]

    # -- submit a queued record when it becomes eligible ---------------------- #

    def _maybe_submit(self, record: QueueRecord, by_name: dict[str, QueueRecord]) -> list[str]:
        for dep_name in record.after:
            dep = by_name.get(dep_name) or self._queue.get(dep_name)
            if dep is None or dep.status in (
                QueueStatus.FAILED,
                QueueStatus.BLOCKED,
                QueueStatus.CANCELLED,
            ):
                record.status = QueueStatus.BLOCKED
                reason = "was removed" if dep is None else dep.status.value
                record.message = f"dependency {dep_name!r} {reason}"
                record.finished_at = time.time()
                self._queue.save(record)
                return [f"{record.name}: BLOCKED ({record.message})"]
            if dep.status is not QueueStatus.DONE:
                return []  # still waiting

        spec = record.launch_spec()
        product = spec.job.product
        if not self._category_usable(product.provider, product.category):
            if record.message != "waiting for quota":
                record.message = "waiting for quota"
                self._queue.save(record)
            return [f"{record.name}: waiting for quota in {product.category}"]

        events: list[str] = []
        try:
            job_id = self._launcher.submit(
                spec, tag=record.name, on_event=lambda m: events.append(f"{record.name}: {m}")
            )
        except UCloudError as exc:
            record.status = QueueStatus.FAILED
            record.message = f"submit failed: {exc}"
            record.finished_at = time.time()
            self._queue.save(record)
            return [*events, f"{record.name}: FAILED ({record.message})"]
        record.status = QueueStatus.SUBMITTED
        record.job_id = job_id
        record.submitted_at = time.time()
        record.message = ""
        self._queue.save(record)
        return events

    def _category_usable(self, provider: str, category: str) -> bool:
        if self._usable_categories is None:
            self._usable_categories = {
                (w.provider, w.category) for w in Catalog(self._client).wallets() if w.usable
            }
        return (provider, category) in self._usable_categories
