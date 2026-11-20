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
    # Folder size including everything below it, from the provider's storage index
    # (``sizeIncludingChildrenInBytes``). ``None`` when the provider doesn't index the drive.
    total_size: int | None = None

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


@dataclass(slots=True)
class IndexSummary:
    """The provider's storage index for one folder, from ``/api/files/visualize``.

    The index is kept by the provider (it rescans changed folders in the background), so
    sizes come back instantly but can lag recent writes; ``updated_at`` says how much.
    """

    path: str
    size: int
    files: int  # files at or below `path`
    dirs: int  # folders below `path`
    updated_at: int | None  # epoch ms the folder's totals were last refreshed
    largest: list[FileEntry]  # biggest files and folders anywhere below, largest first
    complete: bool  # False when `largest` was cut short (it holds at most ~1000 entries)


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

    def list_path(self, path: str, *, sizes: bool = False) -> list[FileEntry]:
        """List the entries directly under ``path`` (e.g. ``/12345/project``).

        With ``sizes``, ask the provider for folder sizes from its storage index
        (:attr:`FileEntry.total_size`).
        """
        params: dict[str, Any] = {"path": path}
        if sizes:
            params["includeSizes"] = "true"
        # Every page: reading only the first 250 silently truncated large directories, and
        # `walk_files` -- hence directory downloads -- skipped everything past entry 250.
        items = iter_browse(self._client, f"{_FILES_BASE}/browse", params)
        entries = [_file_entry(item or {}) for item in items]
        # Directories first, then alphabetical.
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return entries

    def stat(self, path: str, *, sizes: bool = False) -> FileEntry | None:
        """Return metadata for a single path, or ``None`` if it doesn't exist.

        With ``sizes``, a folder's :attr:`FileEntry.total_size` is filled from the
        provider's storage index when it has one.
        """
        params = {"id": path, "includeSizes": "true" if sizes else None}
        try:
            # NOTE: retrieve uses the `id` query param (browse uses `path`).
            data = self._client.get(f"{_FILES_BASE}/retrieve", params=params)
        except APIError as exc:
            # 404 = not found; UCloud also answers 400 when a parent folder in
            # the path does not exist — the same "nothing there" for stat.
            if exc.status_code in (400, 404):
                return None
            raise
        return _file_entry(data if isinstance(data, dict) else {}, path)

    def visualize(self, path: str) -> IndexSummary | None:
        """Read the provider's storage index for ``path`` (what the web UI's usage view shows).

        Returns ``None`` when the drive has no index (the provider doesn't support it, or it is
        still being built -- the first request on a drive can start that build).
        """
        try:
            data = self._client.post(f"{_FILES_BASE}/visualize", json={"path": path})
        except APIError as exc:
            if exc.status_code == 404:  # "storage visualization is not supported"
                return None
            raise
        if not isinstance(data, dict) or data.get("indexing") or not data.get("entries"):
            return None
        entries = [
            FileEntry(
                path=str(e.get("path", "?")),
                type=str(e.get("type", "?")),
                size=_as_int(e.get("sizeInBytes")),
                modified_at=None,
                total_size=_as_int(e.get("sizeInBytes")) if e.get("type") == "DIRECTORY" else None,
            )
            for e in data["entries"]
        ]
        root = entries[0]
        return IndexSummary(
            path=root.path,
            size=root.size or 0,
            files=_as_int(data.get("fileCount")) or 0,
            dirs=_as_int(data.get("directoryCount")) or 0,
            updated_at=_as_int(data.get("lastUpdatedAt")),
            largest=entries[1:],
            complete=bool(data.get("complete")),
        )

    def indexed_usage(
        self, path: str, *, max_depth: int = 1, workers: int = 16
    ) -> dict[str, DirUsage] | None:
        """Folder sizes down to ``max_depth`` from the provider's storage index, like ``du``.

        Lists only the folders above ``max_depth`` (not the whole tree), so it returns in
        about a second however big the folder is. Returns ``None`` when ``path`` itself has
        no indexed size (no index on this drive) -- walk it with :meth:`disk_usage` instead.
        A subfolder missing from the index (typically created or moved in since the last
        refresh) is left out and named in the root's ``errors``. Only ``size``, ``path`` and
        ``depth`` are filled in: the index has no per-folder file counts (see
        :meth:`visualize` for the totals).
        """
        root_path = path.rstrip("/") or "/"
        root = self.stat(root_path, sizes=True)
        if root is None or not root.is_dir or root.total_size is None:
            return None
        top = DirUsage(root_path, 0, size=root.total_size)
        usage = {root_path: top}
        level = [root_path]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for depth in range(1, max_depth + 1):
                next_level: list[str] = []
                for entries in pool.map(lambda p: self.list_path(p, sizes=True), level):
                    for entry in entries:
                        if not entry.is_dir:
                            continue
                        child = entry.path.rstrip("/")
                        if entry.total_size is None:
                            top.errors.append(f"{child}: not in the storage index yet")
                            continue
                        usage[child] = DirUsage(child, depth, size=entry.total_size)
                        next_level.append(child)
                level = next_level
        return usage

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

        A live count: it lists every folder (concurrently, ``workers`` at a time) and adds
        up file sizes, so it is exact but slow on big trees. :meth:`indexed_usage` is the
        instant alternative when the provider indexes the drive. Returns
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


def _file_entry(item: dict[str, Any], default_path: str = "?") -> FileEntry:
    status = item.get("status") or {}
    return FileEntry(
        path=str(item.get("id", default_path)),
        type=str(status.get("type", "?")),
        size=_as_int(status.get("sizeInBytes")),
        modified_at=_as_int(status.get("modifiedAt")),
        total_size=_as_int(status.get("sizeIncludingChildrenInBytes")),
    )


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None
