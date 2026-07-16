"""Browse UCloud drives and files.

UCloud storage is organised into **drives** (file collections). A path looks like
``/<driveId>/folder/subfolder``, where ``<driveId>`` is the numeric id of a drive.
Use :meth:`Files.list_drives` to find drive ids, then :meth:`Files.list_path` to
walk into them. To make a folder available inside a job, add its path to the
job's ``resources`` as a ``file`` entry (see the docs, or ``jobs create --mount``).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from .client import UCloudClient
from .exceptions import APIError

_FILES_BASE = "/api/files"
_DRIVES_BASE = "/api/files/collections"


@dataclass(slots=True)
class Drive:
    id: str
    title: str
    provider: str

    @property
    def path(self) -> str:
        """The root path of this drive, usable as a mount path."""
        return f"/{self.id}"


@dataclass(slots=True)
class FileEntry:
    path: str
    type: str  # FILE | DIRECTORY | SOFT_LINK | ...
    size: int | None
    modified_at: int | None

    @property
    def name(self) -> str:
        return self.path.rstrip("/").rsplit("/", 1)[-1]

    @property
    def is_dir(self) -> bool:
        return self.type == "DIRECTORY"


class Files:
    def __init__(self, client: UCloudClient) -> None:
        self._client = client

    def list_drives(self) -> list[Drive]:
        """List the drives (file collections) you can access."""
        data = self._client.get(f"{_DRIVES_BASE}/browse", params={"itemsPerPage": 250})
        items = data.get("items", []) if isinstance(data, dict) else []
        drives: list[Drive] = []
        for item in items:
            spec = (item or {}).get("specification", {})
            product = spec.get("product", {})
            drives.append(
                Drive(
                    id=str(item.get("id", "?")),
                    title=str(spec.get("title", "") or ""),
                    provider=str(product.get("provider", "") or ""),
                )
            )
        return drives

    def list_path(self, path: str) -> list[FileEntry]:
        """List the entries directly under ``path`` (e.g. ``/12345/project``)."""
        data = self._client.get(f"{_FILES_BASE}/browse", params={"path": path, "itemsPerPage": 250})
        items = data.get("items", []) if isinstance(data, dict) else []
        entries: list[FileEntry] = []
        for item in items:
            status = (item or {}).get("status", {})
            entries.append(
                FileEntry(
                    path=str(item.get("id", "?")),
                    type=str(status.get("type", "?")),
                    size=_as_int(status.get("sizeInBytes")),
                    modified_at=_as_int(status.get("modifiedAt")),
                )
            )
        # Directories first, then alphabetical.
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def stat(self, path: str) -> FileEntry | None:
        """Return metadata for a single path, or ``None`` if it doesn't exist."""
        try:
            # NOTE: retrieve uses the `id` query param (browse uses `path`).
            data = self._client.get(f"{_FILES_BASE}/retrieve", params={"id": path})
        except APIError as exc:
            # 404 = not found; UCloud also answers 400 when a parent folder in
            # the path does not exist — the same "nothing there" for stat.
            if exc.status_code in (400, 404):
                return None
            raise
        status = data.get("status", {}) if isinstance(data, dict) else {}
        return FileEntry(
            path=str(data.get("id", path)),
            type=str(status.get("type", "?")),
            size=_as_int(status.get("sizeInBytes")),
            modified_at=_as_int(status.get("modifiedAt")),
        )

    def walk_files(self, path: str) -> Iterator[FileEntry]:
        """Yield every file (not directory) under ``path``, recursively."""
        for entry in self.list_path(path):
            if entry.is_dir:
                yield from self.walk_files(entry.path)
            elif entry.type == "FILE":
                yield entry

    def mkdir(self, path: str, *, conflict_policy: str = "RENAME") -> None:
        """Create a folder at ``path``."""
        self._client.post(
            f"{_FILES_BASE}/folder",
            json={"items": [{"id": path, "conflictPolicy": conflict_policy}]},
        )

    def trash(self, path: str) -> None:
        """Move a file or folder to the trash."""
        self._client.post(f"{_FILES_BASE}/trash", json={"items": [{"id": path}]})


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None
