"""Drive/file browsing and mount injection."""

from __future__ import annotations

from ucloud_api.cli import _apply_mounts
from ucloud_api.files import Files
from ucloud_api.models import ComputeProduct, JobSpecification, NameAndVersion


class _FakeClient:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def get(self, path, params=None):
        return self._payload


def test_list_drives_parses_id_and_title() -> None:
    payload = {
        "items": [
            {
                "id": "959294",
                "specification": {"title": "Home", "product": {"provider": "ucloud"}},
            }
        ]
    }
    drives = Files(_FakeClient(payload)).list_drives()  # type: ignore[arg-type]
    assert drives[0].id == "959294"
    assert drives[0].title == "Home"
    assert drives[0].path == "/959294"


def test_list_path_sorts_dirs_first() -> None:
    payload = {
        "items": [
            {"id": "/1/zeta.txt", "status": {"type": "FILE", "sizeInBytes": 10}},
            {"id": "/1/alpha", "status": {"type": "DIRECTORY"}},
            {"id": "/1/beta.txt", "status": {"type": "FILE", "sizeInBytes": 20}},
        ]
    }
    entries = Files(_FakeClient(payload)).list_path("/1")  # type: ignore[arg-type]
    assert [e.name for e in entries] == ["alpha", "beta.txt", "zeta.txt"]
    assert entries[0].is_dir
    assert entries[1].size == 20


def _base_spec() -> JobSpecification:
    return JobSpecification(
        application=NameAndVersion(name="pytorch-te", version="26.05"),
        product=ComputeProduct(id="p", category="c", provider="aau"),
    )


def test_apply_mounts_adds_file_resources() -> None:
    spec = _base_spec()
    _apply_mounts(spec, ["/959294/data", "/959294/ref:ro"])
    assert spec.resources is not None
    dumped = [r.model_dump(by_alias=True) for r in spec.resources]
    assert dumped[0] == {"type": "file", "path": "/959294/data", "readOnly": False}
    assert dumped[1] == {"type": "file", "path": "/959294/ref", "readOnly": True}


def test_apply_mounts_preserves_existing_resources() -> None:
    from ucloud_api import params

    spec = _base_spec()
    spec.resources = [params.ingress("link-1")]
    _apply_mounts(spec, ["/959294/data"])
    assert spec.resources is not None
    assert len(spec.resources) == 2
    assert spec.resources[0].type == "ingress"
    assert spec.resources[1].type == "file"


def test_apply_mounts_noop_when_empty() -> None:
    spec = _base_spec()
    _apply_mounts(spec, [])
    assert spec.resources is None
