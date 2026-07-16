"""Job helpers: state reading and SSH endpoint parsing."""

from __future__ import annotations

import pytest

from ucloud_api.exceptions import APIError
from ucloud_api.jobs import Jobs, _parse_ssh_command, _read_state
from ucloud_api.models import JobState


def test_parse_ssh_command() -> None:
    endpoint = _parse_ssh_command("ssh ucloud@ssh.cloud.sdu.dk -p 3421")
    assert endpoint.user == "ucloud"
    assert endpoint.host == "ssh.cloud.sdu.dk"
    assert endpoint.port == 3421
    assert endpoint.command == "ssh ucloud@ssh.cloud.sdu.dk -p 3421"


def test_read_state_ok() -> None:
    assert _read_state({"status": {"state": "RUNNING"}}) == JobState.RUNNING


def test_read_state_missing() -> None:
    with pytest.raises(APIError):
        _read_state({"status": {}})


def test_extend_posts_requested_time() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.posted: tuple[str, dict] | None = None

        def post(self, path, json=None):
            self.posted = (path, json)
            return {}

    client = FakeClient()
    Jobs(client).extend("5471234", hours=8)  # type: ignore[arg-type]
    assert client.posted is not None
    path, body = client.posted
    assert path.endswith("/jobs/extend")
    assert body["items"][0]["jobId"] == "5471234"
    assert body["items"][0]["requestedTime"] == {"hours": 8, "minutes": 0, "seconds": 0}


def test_ssh_endpoint_extracts_from_updates() -> None:
    class FakeClient:
        def get(self, path, params=None):
            return {
                "status": {"state": "RUNNING"},
                "updates": [
                    {"status": "Job is starting"},
                    {"status": "SSH ready: ssh ucloud@ssh.cloud.sdu.dk -p 5000"},
                ],
            }

    endpoint = Jobs(FakeClient()).ssh_endpoint("job-1")  # type: ignore[arg-type]
    assert endpoint is not None
    assert endpoint.port == 5000


def test_ssh_endpoint_none_when_absent() -> None:
    class FakeClient:
        def get(self, path, params=None):
            return {"status": {"state": "IN_QUEUE"}, "updates": [{"status": "queued"}]}

    assert Jobs(FakeClient()).ssh_endpoint("job-1") is None  # type: ignore[arg-type]
