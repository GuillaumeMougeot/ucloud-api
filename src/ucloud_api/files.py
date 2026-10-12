"""Browse UCloud drives and files.

UCloud storage is organised into **drives** (file collections). A path looks like
``/<driveId>/folder/subfolder``, where ``<driveId>`` is the numeric id of a drive.
Use :meth:`Files.list_drives` to find drive ids, then :meth:`Files.list_path` to
walk into them. To make a folder available inside a job, add its path to the
job's ``resources`` as a ``file`` entry (see the docs, or ``jobs create --mount``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any

from .client import UCloudClient, iter_browse
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


@dataclass(slots=True)
class DirUsage:
    """Recursive totals for one folder, as computed by :meth:`Files.disk_usage`."""

    path: str
    depth: int  # 0 for the folder `disk_usage` was called on
    size: int = 0  # bytes of every file at or below this folder
    files: int = 0
    dirs: int = 0
    errors: list[str] = field(default_factory=list)  # subfolders that could not be listed


class Files:
    def __init__(self, client: UCloudClient) -> None:
        self._client = client

    def list_drives(self) -> list[Drive]:
        """List the drives (file collections) you can access."""
        items = iter_browse(self._client, f"{_DRIVES_BASE}/browse")
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
        # Every page: reading only the first 250 silently truncated large directories, and
        # `walk_files` -- hence directory downloads -- skipped everything past entry 250.
        items = iter_browse(self._client, f"{_FILES_BASE}/browse", {"path": path})
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

    def disk_usage(
        self,
        path: str,
        *,
        workers: int = 16,
        on_dir: Callable[[int], None] | None = None,
    ) -> dict[str, DirUsage]:
        """Total file sizes under every folder below ``path``, like ``du``.

        UCloud's provider does not report ``sizeIncludingChildrenInBytes``, so this lists
        every folder (concurrently, ``workers`` at a time) and adds up file sizes. Returns
        one :class:`DirUsage` per folder, keyed by path, with totals that include
        everything below it. ``on_dir`` is called with the running count of folders listed.
        Folders that fail to list are recorded in the root's ``errors`` and skipped; a
        failure on ``path`` itself is raised.
        """
        root = path.rstrip("/") or "/"
        usage: dict[str, DirUsage] = {root: DirUsage(root, 0)}
        parents: dict[str, str] = {}
        listed = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending: dict[Future[list[FileEntry]], str] = {pool.submit(self.list_path, root): root}
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for fut in done:
                    folder = pending.pop(fut)
                    try:
                        entries = fut.result()
                    except APIError as exc:
                        if folder == root:
                            raise
                        usage[root].errors.append(f"{folder}: {exc}")
                        continue
                    node = usage[folder]
                    for entry in entries:
                        if entry.is_dir:
                            child = entry.path.rstrip("/")
                            usage[child] = DirUsage(child, node.depth + 1)
                            parents[child] = folder
                            node.dirs += 1
                            pending[pool.submit(self.list_path, child)] = child
                        elif entry.type == "FILE":
                            node.size += entry.size or 0
                            node.files += 1
                    listed += 1
                    if on_dir is not None:
                        on_dir(listed)
        # Roll each folder's totals into its parent, deepest first.
        for node in sorted(usage.values(), key=lambda u: u.depth, reverse=True):
            parent = parents.get(node.path)
            if parent is not None:
                up = usage[parent]
                up.size += node.size
                up.files += node.files
                up.dirs += node.dirs
        return usage

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
