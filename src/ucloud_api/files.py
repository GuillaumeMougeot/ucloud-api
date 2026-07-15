"""Browse UCloud drives and files.

UCloud storage is organised into **drives** (file collections). A path looks like
``/<driveId>/folder/subfolder``, where ``<driveId>`` is the numeric id of a drive.
Use :meth:`Files.list_drives` to find drive ids, then :meth:`Files.list_path` to
walk into them. To make a folder available inside a job, add its path to the
job's ``resources`` as a ``file`` entry (see the docs, or ``jobs create --mount``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import UCloudClient

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


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None
