"""Fast file upload/download between the local machine and UCloud.

Design rationale
----------------
Transfers are **network-bound to the storage provider**, so the lever that
matters is *concurrency of in-flight transfers*, not local CPU. That rules out
multiprocessing / a native rewrite (complexity without throughput) in favour of
``asyncio`` + a bounded pool that runs **many files at once**.

Protocol facts (verified against cloud.sdu.dk):

* Control-plane calls (``createUpload``/``createDownload``/``createFolder``) go
  through the authenticated client and use the ``id`` field for paths. They must
  carry the ``Project`` header when operating on project resources — the client
  adds it automatically from the active project.
* **Downloads** are a pre-authorised HTTPS ``GET`` (token in the URL); no auth
  header needed.
* **Uploads** use the provider's ``WEBSOCKET_V2`` framed protocol over ``wss``:
  the client sends a *listing* frame per file, the server replies ``OK`` (or
  ``SKIP``), the client streams *chunk* frames, and the server replies
  ``COMPLETED``. The upload endpoint is authorised by a token in its URL. A
  legacy ``CHUNKED`` POST protocol is also supported for providers that offer it.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx
import websockets

from .client import UCloudClient
from .exceptions import APIError, UCloudError
from .files import Files

_FILES_BASE = "/api/files"

DEFAULT_CONCURRENCY = 8
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB
_WS_CHUNK_SIZE = 1024 * 1024  # 1 MiB frames over the websocket
_CONTROL_BATCH = 200  # files per createUpload/createDownload request
_UPLOAD_PROTOCOLS = ["WEBSOCKET_V2", "CHUNKED"]

# WEBSOCKET_V2 message opcodes (see UCloud upload/message.go).
_OP_OK = 0
_OP_CHUNK = 2
_OP_SKIP = 3
_OP_LISTING = 4
_OP_COMPLETED = 5

#: Called with a byte delta as data moves; wire it to a progress bar.
ProgressCallback = Callable[[int], None]


@dataclass(slots=True)
class TransferStats:
    files: int = 0
    total_bytes: int = 0


@dataclass(slots=True)
class _UploadJob:
    local: Path
    endpoint: str
    protocol: str
    token: str
    size: int


@dataclass(slots=True)
class _DownloadJob:
    endpoint: str
    dest: Path
    size: int | None


class Transfer:
    """Upload/download files and directories to/from UCloud."""

    def __init__(self, client: UCloudClient) -> None:
        self._client = client
        self._files = Files(client)
        username = client.username or ""
        self._username_b64 = base64.b64encode(username.encode()).decode() if username else ""

    # -- public API --------------------------------------------------------- #

    def upload(
        self,
        local: Path,
        remote: str,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overwrite: bool = True,
        progress: ProgressCallback | None = None,
        on_start: Callable[[int, int], None] | None = None,
    ) -> TransferStats:
        """Upload a local file or directory tree to ``remote`` on UCloud.

        ``on_start(file_count, total_bytes)`` is called once after planning, so a
        caller can size a progress bar before the transfer begins.
        """
        local = local.expanduser()
        if not local.exists():
            raise UCloudError(f"Local path does not exist: {local}")
        conflict = "REPLACE" if overwrite else "RENAME"

        if local.is_file():
            target = self._resolve_upload_target(remote, local.name)
            (endpoint, protocol, token) = self._create_uploads([target], conflict)[0]
            jobs = [_UploadJob(local, endpoint, protocol, token, local.stat().st_size)]
        else:
            jobs = self._plan_directory_upload(local, remote, conflict)

        total = sum(j.size for j in jobs)
        if on_start:
            on_start(len(jobs), total)
        asyncio.run(self._run_uploads(jobs, concurrency, chunk_size, progress))
        return TransferStats(files=len(jobs), total_bytes=total)

    def download(
        self,
        remote: str,
        local: Path,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        progress: ProgressCallback | None = None,
        on_start: Callable[[int, int], None] | None = None,
    ) -> TransferStats:
        """Download a UCloud file or directory tree to ``local``.

        ``on_start(file_count, total_bytes)`` is called once after planning.
        """
        local = local.expanduser()
        info = self._files.stat(remote)
        if info is None:
            raise UCloudError(f"Remote path not found: {remote}")

        if info.is_dir:
            jobs = self._plan_directory_download(remote, local)
        else:
            dest = local / info.path.rsplit("/", 1)[-1] if local.is_dir() else local
            endpoint = self._create_downloads([remote])[0]
            jobs = [_DownloadJob(endpoint, dest, info.size)]

        total = sum(j.size or 0 for j in jobs)
        if on_start:
            on_start(len(jobs), total)
        asyncio.run(self._run_downloads(jobs, concurrency, chunk_size, progress))
        return TransferStats(files=len(jobs), total_bytes=total)

    # -- planning ----------------------------------------------------------- #

    def _plan_directory_upload(self, local: Path, remote: str, conflict: str) -> list[_UploadJob]:
        remote_root = remote.rstrip("/")
        local_files = [p for p in local.rglob("*") if p.is_file()]
        if not local_files:
            return []

        # Create the remote directory tree (shallowest first), ignoring existing.
        dirs = sorted(
            {str(PurePosixPath(remote_root) / p.relative_to(local).parent) for p in local_files},
            key=lambda d: d.count("/"),
        )
        for d in dirs:
            self._create_folder(d)

        remote_paths = [
            str(PurePosixPath(remote_root) / p.relative_to(local).as_posix()) for p in local_files
        ]
        endpoints = self._create_uploads(remote_paths, conflict)
        return [
            _UploadJob(f, endpoint, protocol, token, f.stat().st_size)
            for f, (endpoint, protocol, token) in zip(local_files, endpoints, strict=True)
        ]

    def _plan_directory_download(self, remote: str, local: Path) -> list[_DownloadJob]:
        remote_root = remote.rstrip("/")
        entries = list(self._files.walk_files(remote_root))
        if not entries:
            return []
        endpoints = self._create_downloads([e.path for e in entries])
        jobs: list[_DownloadJob] = []
        for entry, endpoint in zip(entries, endpoints, strict=True):
            rel = entry.path[len(remote_root) :].lstrip("/")
            jobs.append(_DownloadJob(endpoint, local / rel, entry.size))
        return jobs

    def _resolve_upload_target(self, remote: str, name: str) -> str:
        """If ``remote`` is an existing directory (or ends with /), append name."""
        if remote.endswith("/"):
            return remote.rstrip("/") + "/" + name
        info = self._files.stat(remote)
        if info is not None and info.is_dir:
            return remote.rstrip("/") + "/" + name
        return remote

    # -- control plane (sync, authenticated) -------------------------------- #

    def _create_uploads(self, paths: list[str], conflict: str) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        for batch in _batched(paths, _CONTROL_BATCH):
            items = [
                {"id": p, "supportedProtocols": _UPLOAD_PROTOCOLS, "conflictPolicy": conflict}
                for p in batch
            ]
            data = self._client.post(f"{_FILES_BASE}/upload", json={"items": items})
            try:
                out.extend(
                    (self._normalize_endpoint(r["endpoint"]), r["protocol"], r["token"])
                    for r in data["responses"]
                )
            except (KeyError, TypeError) as exc:
                raise APIError(f"Unexpected createUpload response: {data!r}") from exc
        return out

    def _create_downloads(self, paths: list[str]) -> list[str]:
        out: list[str] = []
        for batch in _batched(paths, _CONTROL_BATCH):
            items = [{"id": p} for p in batch]
            data = self._client.post(f"{_FILES_BASE}/download", json={"items": items})
            try:
                out.extend(self._normalize_endpoint(r["endpoint"]) for r in data["responses"])
            except (KeyError, TypeError) as exc:
                raise APIError(f"Unexpected createDownload response: {data!r}") from exc
        return out

    def _create_folder(self, path: str) -> None:
        # A REJECT conflict on an existing folder raises; that "already there"
        # case is safe to ignore.
        with contextlib.suppress(APIError):
            self._client.post(
                f"{_FILES_BASE}/folder",
                json={"items": [{"id": path, "conflictPolicy": "REJECT"}]},
            )

    def _normalize_endpoint(self, endpoint: str) -> str:
        if endpoint.startswith(("http://", "https://", "ws://", "wss://")):
            return endpoint
        return self._client.base_url.rstrip("/") + "/" + endpoint.lstrip("/")

    # -- data plane (async, no bearer) -------------------------------------- #

    async def _run_uploads(
        self,
        jobs: list[_UploadJob],
        concurrency: int,
        chunk_size: int,
        progress: ProgressCallback | None,
    ) -> None:
        sem = asyncio.Semaphore(max(1, concurrency))

        async def worker(job: _UploadJob) -> None:
            async with sem:
                if job.protocol == "WEBSOCKET_V2":
                    await self._upload_ws(job, progress)
                elif job.protocol == "CHUNKED":
                    await self._upload_chunked(job, chunk_size, progress)
                else:
                    raise UCloudError(f"Unsupported upload protocol: {job.protocol}")

        await asyncio.gather(*(worker(j) for j in jobs))

    async def _upload_ws(self, job: _UploadJob, progress: ProgressCallback | None) -> None:
        """Upload one file via the WEBSOCKET_V2 framed protocol (file id 1)."""
        modified_ms = int(job.local.stat().st_mtime * 1000)
        loop = asyncio.get_running_loop()
        async with websockets.connect(
            job.endpoint, max_size=None, additional_headers={"Origin": "https://cloud.sdu.dk"}
        ) as ws:
            await ws.send(_listing_frame(1, job.size, modified_ms, ""))
            streamed = False
            while True:
                try:
                    frame = await asyncio.wait_for(ws.recv(), timeout=120)
                except TimeoutError as exc:
                    raise UCloudError(f"Upload stalled for {job.local.name}") from exc
                if isinstance(frame, str):
                    continue  # keepalive / pong
                for opcode, _value in _parse_server_frame(frame):
                    if opcode == _OP_OK and not streamed:
                        with open(job.local, "rb") as f:
                            while True:
                                chunk = await loop.run_in_executor(None, f.read, _WS_CHUNK_SIZE)
                                if not chunk:
                                    break
                                await ws.send(_chunk_frame(1, chunk))
                                if progress:
                                    progress(len(chunk))
                        streamed = True
                    elif opcode == _OP_SKIP:
                        if progress:
                            progress(job.size)
                        return
                    elif opcode == _OP_COMPLETED:
                        return

    async def _upload_chunked(
        self, job: _UploadJob, chunk_size: int, progress: ProgressCallback | None
    ) -> None:
        """Legacy CHUNKED protocol: sequential offset-based POSTs to the endpoint."""
        loop = asyncio.get_running_loop()
        timeout = httpx.Timeout(connect=30.0, read=None, write=None, pool=None)
        async with httpx.AsyncClient(timeout=timeout) as ac:
            offset = 0
            with open(job.local, "rb") as f:
                while True:
                    chunk = await loop.run_in_executor(None, f.read, chunk_size)
                    if not chunk and offset > 0:
                        break
                    headers = {
                        "Chunked-Upload-Token": job.token,
                        "Chunked-Upload-Offset": str(offset),
                        "Chunked-Upload-Total-Size": str(job.size),
                    }
                    if self._username_b64:
                        headers["UCloud-Username"] = self._username_b64
                    resp = await ac.post(job.endpoint, content=chunk, headers=headers)
                    if resp.status_code >= 400:
                        raise APIError(
                            f"Upload chunk failed for {job.local.name} ({resp.status_code})",
                            status_code=resp.status_code,
                        )
                    offset += len(chunk)
                    if progress and chunk:
                        progress(len(chunk))
                    if not chunk:
                        break

    async def _run_downloads(
        self,
        jobs: list[_DownloadJob],
        concurrency: int,
        chunk_size: int,
        progress: ProgressCallback | None,
    ) -> None:
        sem = asyncio.Semaphore(max(1, concurrency))
        timeout = httpx.Timeout(connect=30.0, read=None, write=None, pool=None)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as ac:

            async def worker(job: _DownloadJob) -> None:
                async with sem:
                    await self._download_one(ac, job, chunk_size, progress)

            await asyncio.gather(*(worker(j) for j in jobs))

    async def _download_one(
        self,
        ac: httpx.AsyncClient,
        job: _DownloadJob,
        chunk_size: int,
        progress: ProgressCallback | None,
    ) -> None:
        job.dest.parent.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        async with ac.stream("GET", job.endpoint) as resp:
            if resp.status_code >= 400:
                raise APIError(
                    f"Download failed for {job.dest.name} ({resp.status_code})",
                    status_code=resp.status_code,
                )
            with open(job.dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size):
                    await loop.run_in_executor(None, f.write, chunk)
                    if progress:
                        progress(len(chunk))


# -- WEBSOCKET_V2 wire format (big-endian) ---------------------------------- #


def _listing_frame(file_id: int, size: int, modified_ms: int, rel_path: str) -> bytes:
    path = rel_path.encode()
    return (
        bytes([_OP_LISTING])
        + struct.pack(">i", file_id)
        + struct.pack(">q", size)
        + struct.pack(">q", modified_ms)
        + struct.pack(">i", len(path))
        + path
    )


def _chunk_frame(file_id: int, data: bytes) -> bytes:
    return bytes([_OP_CHUNK]) + struct.pack(">i", file_id) + data


def _parse_server_frame(frame: bytes) -> list[tuple[int, int]]:
    """Decode a server frame into a list of (opcode, int-value) messages."""
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(frame):
        opcode = frame[i]
        i += 1
        if opcode in (_OP_OK, _OP_SKIP, _OP_COMPLETED):
            if i + 4 > len(frame):
                break
            (value,) = struct.unpack_from(">i", frame, i)
            i += 4
            out.append((opcode, value))
        else:
            break
    return out


def _batched(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
