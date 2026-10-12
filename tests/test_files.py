"""Drive/file browsing and mount injection."""

from __future__ import annotations

import pytest

from ucloud_api.cli import _apply_mounts
from ucloud_api.exceptions import APIError
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


class _PagedClient:
    """Serves `items` in pages of `size`, with a `next` token, like UCloud's browse endpoints."""

    def __init__(self, items: list, size: int) -> None:
        self._items, self._size, self.calls = items, size, []

    def get(self, path, params=None):
        params = dict(params or {})
        self.calls.append(params)
        start = int(params.get("next", 0))
        page = self._items[start : start + self._size]
        nxt = start + self._size
        return {"items": page, "next": str(nxt) if nxt < len(self._items) else None}


def test_list_path_reads_every_page() -> None:
    # The regression: 2,050 entries listed as 250 because only page one was read.
    items = [{"id": f"/1/part-{i:05d}.parquet", "status": {"type": "FILE"}} for i in range(2050)]
    client = _PagedClient(items, size=250)
    entries = Files(client).list_path("/1")  # type: ignore[arg-type]
    assert len(entries) == 2050
    assert len(client.calls) == 9
    assert all(c["path"] == "/1" for c in client.calls)
    assert client.calls[1]["next"] == "250"


def test_walk_files_does_not_truncate_large_directories() -> None:
    items = [{"id": f"/1/f{i}.jpg", "status": {"type": "FILE"}} for i in range(600)]
    assert len(list(Files(_PagedClient(items, 250)).walk_files("/1"))) == 600  # type: ignore[arg-type]


def test_list_drives_reads_every_page() -> None:
    items = [{"id": str(i), "specification": {"title": f"d{i}"}} for i in range(300)]
    assert len(Files(_PagedClient(items, 250)).list_drives()) == 300  # type: ignore[arg-type]


class _TreeClient:
    """Serves `browse` for an in-memory tree; folders in `broken` answer 403."""

    def __init__(self, tree: dict[str, list[tuple[str, str, int]]], broken=()) -> None:
        self._tree, self._broken = tree, set(broken)

    def get(self, path, params=None):
        folder = (params or {})["path"]
        if folder in self._broken:
            raise APIError("forbidden", status_code=403)
        return {
            "items": [
                {"id": f"{folder}/{name}", "status": {"type": kind, "sizeInBytes": size}}
                for name, kind, size in self._tree[folder]
            ]
        }


_TREE = {
    "/1": [("a", "DIRECTORY", 0), ("top.bin", "FILE", 5)],
    "/1/a": [("b", "DIRECTORY", 0), ("x", "FILE", 10), ("link", "SOFT_LINK", 99)],
    "/1/a/b": [("y", "FILE", 100), ("z", "FILE", 1000)],
}


def test_disk_usage_rolls_sizes_up_to_every_ancestor() -> None:
    usage = Files(_TreeClient(_TREE)).disk_usage("/1/", workers=4)  # type: ignore[arg-type]
    assert usage["/1"].size == 1115 and usage["/1"].files == 4 and usage["/1"].dirs == 2
    assert usage["/1/a"].size == 1110 and usage["/1/a"].files == 3 and usage["/1/a"].depth == 1
    assert usage["/1/a/b"].size == 1100 and usage["/1/a/b"].depth == 2


def test_disk_usage_skips_unreadable_subfolders_but_raises_on_root() -> None:
    usage = Files(_TreeClient(_TREE, broken={"/1/a/b"})).disk_usage("/1")  # type: ignore[arg-type]
    assert usage["/1"].size == 15
    assert usage["/1"].errors and usage["/1"].errors[0].startswith("/1/a/b:")
    with pytest.raises(APIError):
        Files(_TreeClient(_TREE, broken={"/1"})).disk_usage("/1")  # type: ignore[arg-type]
