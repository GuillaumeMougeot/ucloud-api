"""High-level operations on UCloud jobs and SSH keys."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from .client import UCloudClient
from .exceptions import APIError, JobFailedError, JobTimeoutError, UCloudError
from .models import AppParameterValue, JobSpecification, JobState

_PARAM_ADAPTER: TypeAdapter[AppParameterValue] = TypeAdapter(AppParameterValue)

_JOBS_BASE = "/api/jobs"
_SSH_BASE = "/api/ssh"

# The message UCloud posts when SSH is ready, e.g.
# "ssh ucloud@ssh.cloud.sdu.dk -p 3421"
_SSH_CMD_RE = re.compile(r"ssh\s+\S+@\S+\s+-p\s+\d+")


@dataclass(slots=True)
class SSHEndpoint:
    user: str
    host: str
    port: int

    @property
    def command(self) -> str:
        return f"ssh {self.user}@{self.host} -p {self.port}"


class Jobs:
    """CRUD + waiting helpers for UCloud jobs."""

    def __init__(self, client: UCloudClient) -> None:
        self._client = client

    # -- creation ----------------------------------------------------------- #

    def create(self, spec: JobSpecification) -> str:
        """Submit a job and return its id."""
        body = {"items": [spec.model_dump(by_alias=True, exclude_none=True)]}
        data = self._client.post(_JOBS_BASE, json=body)
        try:
            return cast(str, data["responses"][0]["id"])
        except (KeyError, IndexError, TypeError) as exc:
            raise APIError(f"Unexpected create-job response: {data!r}") from exc

    # -- reading ------------------------------------------------------------ #

    def retrieve(self, job_id: str, *, include_updates: bool = True) -> dict[str, Any]:
        params = {
            "id": job_id,
            "includeUpdates": str(include_updates).lower(),
            "includeApplication": "true",
        }
        return cast("dict[str, Any]", self._client.get(f"{_JOBS_BASE}/retrieve", params=params))

    def state(self, job_id: str) -> JobState:
        job = self.retrieve(job_id, include_updates=False)
        return _read_state(job)

    def browse(self, *, items_per_page: int = 50) -> list[dict[str, Any]]:
        data = self._client.get(f"{_JOBS_BASE}/browse", params={"itemsPerPage": items_per_page})
        return data.get("items", []) if isinstance(data, dict) else []

    # -- lifecycle ---------------------------------------------------------- #

    def terminate(self, job_id: str) -> None:
        self._client.post(f"{_JOBS_BASE}/terminate", json={"items": [{"id": job_id}]})

    def extend(self, job_id: str, *, hours: int, minutes: int = 0) -> None:
        """Add time to a running job's allocation (the GUI's +1h/+8h buttons)."""
        self._client.post(
            f"{_JOBS_BASE}/extend",
            json={
                "items": [
                    {
                        "jobId": job_id,
                        "requestedTime": {"hours": hours, "minutes": minutes, "seconds": 0},
                    }
                ]
            },
        )

    # -- waiting ------------------------------------------------------------ #

    def wait_until_running(
        self,
        job_id: str,
        *,
        timeout: float = 900.0,
        poll_interval: float = 5.0,
        on_state: Callable[[JobState], None] | None = None,
    ) -> dict[str, Any]:
        """Block until the job is RUNNING; return the full job payload.

        Raises :class:`JobFailedError` if the job reaches a terminal state first
        and :class:`JobTimeoutError` if ``timeout`` elapses.
        """
        deadline = time.monotonic() + timeout
        last_state: JobState | None = None
        while True:
            job = self.retrieve(job_id)
            state = _read_state(job)
            if state != last_state:
                last_state = state
                if on_state is not None:
                    on_state(state)
            if state == JobState.RUNNING:
                return job
            if state.is_terminal or state == JobState.CANCELING:
                raise JobFailedError(
                    f"Job {job_id} reached {state.value} before running.",
                    state=state.value,
                )
            if time.monotonic() >= deadline:
                raise JobTimeoutError(f"Job {job_id} still {state.value} after {timeout:.0f}s.")
            time.sleep(poll_interval)

    # -- ssh ---------------------------------------------------------------- #

    def ssh_endpoint(self, job_id: str) -> SSHEndpoint | None:
        """Extract the SSH connection details posted in the job's updates.

        UCloud sends a message like ``ssh ucloud@ssh.cloud.sdu.dk -p 3421`` once
        an SSH-enabled job is running. Returns ``None`` if not advertised yet.
        """
        job = self.retrieve(job_id, include_updates=True)
        updates = job.get("updates", []) if isinstance(job, dict) else []
        for update in updates:
            message = (update or {}).get("status") or ""
            match = _SSH_CMD_RE.search(message)
            if match:
                return _parse_ssh_command(match.group(0))
        return None


class SSHKeys:
    """Register the public keys that SSH-enabled jobs will accept."""

    def __init__(self, client: UCloudClient) -> None:
        self._client = client

    def add(self, title: str, public_key: str) -> str:
        body = {"items": [{"title": title, "key": public_key.strip()}]}
        data = self._client.post(_SSH_BASE, json=body)
        try:
            return cast(str, data["responses"][0]["id"])
        except (KeyError, IndexError, TypeError) as exc:
            raise APIError(f"Unexpected add-ssh-key response: {data!r}") from exc

    def list(self) -> list[dict[str, Any]]:
        data = self._client.get(f"{_SSH_BASE}/browse", params={"itemsPerPage": 250})
        return data.get("items", []) if isinstance(data, dict) else []


#: The only top-level fields we export, mapped to the snake_case keys our spec
#: files and models use. Everything else the API returns (labels, hostname,
#: allowDuplicateJob, resolved* ...) is server-managed noise and is dropped.
_EXPORTED_SPEC_KEYS = {
    "application": "application",
    "product": "product",
    "name": "name",
    "replicas": "replicas",
    "parameters": "parameters",
    "resources": "resources",
    "timeAllocation": "time_allocation",
    "openedFile": "opened_file",
    "sshEnabled": "ssh_enabled",
}


def _strip_nulls(obj: Any) -> Any:
    """Recursively drop ``None`` values (TOML cannot represent null)."""
    if isinstance(obj, dict):
        return {k: _strip_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_nulls(v) for v in obj]
    return obj


def _normalize_param(param: Any) -> Any:
    """Reduce a fat API parameter object to its minimal tagged form.

    UCloud returns every ``AppParameterValue`` with all fields present (mostly
    null/empty). Validating through our typed union keeps only the fields that
    matter for that ``type``. Unknown types fall back to a plain null-strip.
    """
    if isinstance(param, dict) and isinstance(param.get("type"), str):
        try:
            model = _PARAM_ADAPTER.validate_python(param)
        except ValidationError:
            return _strip_nulls(param)
        return model.model_dump(exclude_none=True)
    return _strip_nulls(param)


def specification_to_spec_dict(job: dict[str, Any]) -> dict[str, Any]:
    """Turn a retrieved job into a clean dict suitable for writing as a spec TOML.

    The output round-trips back through :class:`JobSpecification` and
    ``ucloud jobs create``, so ``ucloud jobs show <id>`` can seed a new run from
    an old one.
    """
    spec = job.get("specification")
    if not isinstance(spec, dict):
        raise APIError(f"Job payload has no specification: {job!r}")

    out: dict[str, Any] = {}
    for api_key, spec_key in _EXPORTED_SPEC_KEYS.items():
        value = spec.get(api_key)
        if value is None:
            continue
        if api_key == "parameters" and isinstance(value, dict):
            value = {name: _normalize_param(p) for name, p in value.items() if p is not None}
            if not value:
                continue
        elif api_key == "resources" and isinstance(value, list):
            value = [_normalize_param(r) for r in value if r is not None]
            if not value:
                continue
        else:
            value = _strip_nulls(value)
        out[spec_key] = value
    return out


def _read_state(job: dict[str, Any]) -> JobState:
    try:
        raw = job["status"]["state"]
    except (KeyError, TypeError) as exc:
        raise APIError(f"Job payload has no status.state: {job!r}") from exc
    try:
        return JobState(raw)
    except ValueError as exc:
        raise UCloudError(f"Unknown job state {raw!r}") from exc


def _parse_ssh_command(command: str) -> SSHEndpoint:
    # command looks like: ssh ucloud@ssh.cloud.sdu.dk -p 3421
    parts = command.split()
    user_host = parts[1]
    user, _, host = user_host.partition("@")
    port = int(parts[parts.index("-p") + 1])
    return SSHEndpoint(user=user, host=host, port=port)
