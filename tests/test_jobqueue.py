"""Queue storage and scheduler tick transitions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from ucloud_api.exceptions import UCloudError
from ucloud_api.jobqueue import Queue, QueueRecord, QueueStatus, Scheduler

_SPEC: dict[str, Any] = {
    "application": {"name": "app", "version": "1"},
    "product": {"id": "p", "category": "gpu-cat", "provider": "ucloud"},
    "schedule": {"auto_extend": "1h", "max_time": "2h"},
}

# A batch spec: [setup] run means the script records an exit code before it returns, so a
# missing one is evidence the run never finished.
_BATCH_SPEC: dict[str, Any] = {
    **_SPEC,
    "sync": {"local": ".", "remote": "/123/repos/p"},
    "setup": {"run": "python train.py"},
}


def _record(name: str, **kwargs: Any) -> QueueRecord:
    return QueueRecord(name=name, spec=dict(_SPEC), base_dir=".", **kwargs)


# -- storage ----------------------------------------------------------------- #


def test_queue_roundtrip(tmp_path: Path) -> None:
    queue = Queue(tmp_path)
    queue.add(_record("a"))
    loaded = queue.get("a")
    assert loaded is not None
    assert loaded.status is QueueStatus.QUEUED
    assert loaded.spec["product"]["category"] == "gpu-cat"
    assert [r.name for r in queue.all()] == ["a"]


def test_queue_rejects_duplicate_names(tmp_path: Path) -> None:
    queue = Queue(tmp_path)
    queue.add(_record("a"))
    with pytest.raises(UCloudError, match="already exists"):
        queue.add(_record("a"))


def test_queue_rejects_unknown_dependency(tmp_path: Path) -> None:
    with pytest.raises(UCloudError, match="no queued job"):
        Queue(tmp_path).add(_record("b", after=["missing"]))


# -- scheduler ---------------------------------------------------------------- #


class _FakeJobs:
    def __init__(self, status: dict[str, Any]) -> None:
        self.status = status
        self.extended: list[tuple[str, int, int]] = []
        self.terminated: list[str] = []

    def retrieve(self, job_id: str) -> dict[str, Any]:
        return {"id": job_id, "status": self.status}

    def extend(self, job_id: str, *, hours: int, minutes: int = 0) -> None:
        self.extended.append((job_id, hours, minutes))


class _FakeLauncher:
    def __init__(self, exit_code: int | None = None, fail: bool = False) -> None:
        self.exit_code = exit_code
        self.fail = fail
        self.submitted: list[str] = []

    def submit(self, spec: Any, *, tag: str, on_event: Any = None) -> str:
        if self.fail:
            raise UCloudError("boom")
        self.submitted.append(tag)
        return f"job-{tag}"

    def read_exit_code(self, spec: Any, tag: str) -> int | None:
        return self.exit_code


def _scheduler(
    tmp_path: Path,
    *,
    jobs: _FakeJobs | None = None,
    launcher: _FakeLauncher | None = None,
    usable: bool = True,
) -> tuple[Scheduler, Queue]:
    queue = Queue(tmp_path)
    sched = Scheduler.__new__(Scheduler)
    sched._client = None  # type: ignore[assignment]
    sched._queue = queue
    sched._jobs = jobs or _FakeJobs({})  # type: ignore[assignment]
    sched._launcher = launcher or _FakeLauncher()  # type: ignore[assignment]
    sched._usable_categories = {("ucloud", "gpu-cat")} if usable else set()
    # Keep the per-tick cache: tick() resets it, so pre-seed via monkeypatched method.
    sched._category_usable = lambda p, c: (
        (p, c)
        in (  # type: ignore[method-assign]
            {("ucloud", "gpu-cat")} if usable else set()
        )
    )
    return sched, queue


def test_tick_submits_when_eligible(tmp_path: Path) -> None:
    launcher = _FakeLauncher()
    sched, queue = _scheduler(tmp_path, launcher=launcher)
    queue.add(_record("a"))
    sched.tick()
    rec = queue.get("a")
    assert rec is not None and rec.status is QueueStatus.SUBMITTED
    assert rec.job_id == "job-a"
    assert launcher.submitted == ["a"]


def test_tick_waits_for_quota(tmp_path: Path) -> None:
    sched, queue = _scheduler(tmp_path, usable=False)
    queue.add(_record("a"))
    events = sched.tick()
    rec = queue.get("a")
    assert rec is not None and rec.status is QueueStatus.QUEUED
    assert any("waiting for quota" in e for e in events)


def test_tick_dependency_gating(tmp_path: Path) -> None:
    launcher = _FakeLauncher()
    sched, queue = _scheduler(tmp_path, launcher=launcher)
    queue.add(_record("a"))
    queue.add(_record("b", after=["a"]))
    # a is still QUEUED at scan time, so b must wait; a itself submits.
    sched.tick()
    rec_b = queue.get("b")
    assert rec_b is not None and rec_b.status is QueueStatus.QUEUED
    # Once a is DONE, b submits.
    rec_a = queue.get("a")
    assert rec_a is not None
    rec_a.status = QueueStatus.DONE
    queue.save(rec_a)
    sched.tick()
    rec_b = queue.get("b")
    assert rec_b is not None and rec_b.status is QueueStatus.SUBMITTED


def test_tick_blocks_dependents_of_failures(tmp_path: Path) -> None:
    sched, queue = _scheduler(tmp_path)
    queue.add(_record("a"))
    queue.add(_record("b", after=["a"]))
    rec_a = queue.get("a")
    assert rec_a is not None
    rec_a.status = QueueStatus.FAILED
    queue.save(rec_a)
    sched.tick()
    rec_b = queue.get("b")
    assert rec_b is not None and rec_b.status is QueueStatus.BLOCKED


def test_tick_marks_running_then_done_with_exit_code(tmp_path: Path) -> None:
    jobs = _FakeJobs({"state": "RUNNING"})
    launcher = _FakeLauncher(exit_code=0)
    sched, queue = _scheduler(tmp_path, jobs=jobs, launcher=launcher)
    queue.add(_record("a", status=QueueStatus.SUBMITTED, job_id="j1"))
    sched.tick()
    rec = queue.get("a")
    assert rec is not None and rec.status is QueueStatus.RUNNING

    jobs.status = {"state": "SUCCESS"}
    sched.tick()
    rec = queue.get("a")
    assert rec is not None and rec.status is QueueStatus.DONE


def test_tick_fails_on_nonzero_exit_code(tmp_path: Path) -> None:
    jobs = _FakeJobs({"state": "SUCCESS"})
    launcher = _FakeLauncher(exit_code=3)
    sched, queue = _scheduler(tmp_path, jobs=jobs, launcher=launcher)
    queue.add(_record("a", status=QueueStatus.RUNNING, job_id="j1"))
    sched.tick()
    rec = queue.get("a")
    assert rec is not None and rec.status is QueueStatus.FAILED
    assert "exit code 3" in rec.message


def test_tick_fails_batch_run_that_left_no_exit_code(tmp_path: Path) -> None:
    """A terminated batch job: UCloud reports SUCCESS, but the run never finished.

    Regression: this was read as success, so `--after` dependents launched off a job the
    user had cancelled.
    """
    jobs = _FakeJobs({"state": "SUCCESS"})
    launcher = _FakeLauncher(exit_code=None)
    sched, queue = _scheduler(tmp_path, jobs=jobs, launcher=launcher)
    queue.add(
        QueueRecord(
            name="a", spec=dict(_BATCH_SPEC), base_dir=".", status=QueueStatus.RUNNING, job_id="j1"
        )
    )
    sched.tick()
    rec = queue.get("a")
    assert rec is not None and rec.status is QueueStatus.FAILED
    assert "did not finish" in rec.message


def test_tick_blocks_dependents_of_a_terminated_batch_run(tmp_path: Path) -> None:
    jobs = _FakeJobs({"state": "SUCCESS"})
    launcher = _FakeLauncher(exit_code=None)
    sched, queue = _scheduler(tmp_path, jobs=jobs, launcher=launcher)
    queue.add(
        QueueRecord(
            name="a", spec=dict(_BATCH_SPEC), base_dir=".", status=QueueStatus.RUNNING, job_id="j1"
        )
    )
    queue.add(QueueRecord(name="b", spec=dict(_BATCH_SPEC), base_dir=".", after=["a"]))
    sched.tick()
    rec_b = queue.get("b")
    assert rec_b is not None and rec_b.status is QueueStatus.BLOCKED
    assert launcher.submitted == [], "a cancelled dependency must not satisfy afterok"


def test_tick_passes_batch_run_that_recorded_zero(tmp_path: Path) -> None:
    jobs = _FakeJobs({"state": "SUCCESS"})
    launcher = _FakeLauncher(exit_code=0)
    sched, queue = _scheduler(tmp_path, jobs=jobs, launcher=launcher)
    queue.add(
        QueueRecord(
            name="a", spec=dict(_BATCH_SPEC), base_dir=".", status=QueueStatus.RUNNING, job_id="j1"
        )
    )
    sched.tick()
    rec = queue.get("a")
    assert rec is not None and rec.status is QueueStatus.DONE


def test_tick_without_run_command_trusts_job_state(tmp_path: Path) -> None:
    """An initScript spec writes no exit file, so SUCCESS alone has to mean success."""
    jobs = _FakeJobs({"state": "SUCCESS"})
    launcher = _FakeLauncher(exit_code=None)
    sched, queue = _scheduler(tmp_path, jobs=jobs, launcher=launcher)
    queue.add(_record("a", status=QueueStatus.RUNNING, job_id="j1"))
    sched.tick()
    rec = queue.get("a")
    assert rec is not None and rec.status is QueueStatus.DONE


def test_tick_extends_job_low_on_time(tmp_path: Path) -> None:
    now_ms = time.time() * 1000
    # 55m elapsed of a 1h allocation: extending by 1h stays within the 2h cap.
    jobs = _FakeJobs(
        {"state": "RUNNING", "startedAt": now_ms - 3_300_000, "expiresAt": now_ms + 300_000}
    )
    sched, queue = _scheduler(tmp_path, jobs=jobs)
    queue.add(_record("a", status=QueueStatus.RUNNING, job_id="j1"))
    events = sched.tick()
    assert jobs.extended == [("j1", 1, 0)]
    assert any("extended" in e for e in events)
    rec = queue.get("a")
    assert rec is not None and rec.extended_minutes == 60


def test_tick_respects_max_time_cap(tmp_path: Path) -> None:
    now_ms = time.time() * 1000
    # Already at the 2h cap: extending by 1h would exceed it.
    jobs = _FakeJobs(
        {"state": "RUNNING", "startedAt": now_ms - 6_900_000, "expiresAt": now_ms + 300_000}
    )
    sched, queue = _scheduler(tmp_path, jobs=jobs)
    queue.add(_record("a", status=QueueStatus.RUNNING, job_id="j1"))
    events = sched.tick()
    assert jobs.extended == []
    assert any("max_time" in e for e in events)


def test_record_json_is_readable(tmp_path: Path) -> None:
    queue = Queue(tmp_path)
    queue.add(_record("a"))
    data = json.loads((tmp_path / "a.json").read_text(encoding="utf-8"))
    assert data["status"] == "QUEUED"
    assert data["spec"]["application"]["name"] == "app"
