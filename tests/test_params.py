"""AppParameterValue serialization matches the UCloud wire format."""

from __future__ import annotations

from ucloud_api import params
from ucloud_api.models import JobSpecification


def test_text_param_serializes_with_tag() -> None:
    assert params.text("hi").model_dump(by_alias=True) == {"type": "text", "value": "hi"}


def test_file_param_uses_camelcase_read_only() -> None:
    dumped = params.file("/123/data", read_only=True).model_dump(by_alias=True)
    assert dumped == {"type": "file", "path": "/123/data", "readOnly": True}


def test_directory_is_a_file_param() -> None:
    assert params.directory("/123/x").type == "file"


def test_peer_param_serializes_job_id() -> None:
    dumped = params.peer("host", "job-1").model_dump(by_alias=True)
    assert dumped == {"type": "peer", "hostname": "host", "jobId": "job-1"}


def test_spec_dump_is_camelcase_and_drops_none() -> None:
    from ucloud_api.models import ComputeProduct, NameAndVersion

    spec = JobSpecification(
        application=NameAndVersion(name="pytorch-te", version="1.0"),
        product=ComputeProduct(id="u1-gpu-1", category="u1-gpu", provider="ucloud"),
        ssh_enabled=True,
        parameters={"opt": params.text("v")},
    )
    dumped = spec.model_dump(by_alias=True, exclude_none=True)
    assert dumped["sshEnabled"] is True
    assert dumped["parameters"]["opt"] == {"type": "text", "value": "v"}
    assert "name" not in dumped  # None fields are dropped
    assert "timeAllocation" not in dumped


def test_spec_roundtrips_from_dict_like_toml() -> None:
    data = {
        "application": {"name": "pytorch-te", "version": "1.0"},
        "product": {"id": "u1-gpu-1", "category": "u1-gpu", "provider": "ucloud"},
        "ssh_enabled": True,
        "parameters": {"folder": {"type": "file", "path": "/1/p", "read_only": False}},
    }
    spec = JobSpecification.model_validate(data)
    assert spec.parameters["folder"].type == "file"
    assert spec.ssh_enabled is True
