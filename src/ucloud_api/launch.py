"""Turn a :class:`LaunchSpec` into a submitted job: sync, setup, mount, create.

The pipeline (each step optional, driven by the spec's tool sections):

1. ``[sync]`` — incrementally push the local working tree to a drive folder
   (``.gitignore`` respected via ``git ls-files`` when available) and mount it.
2. ``[setup]`` — generate a shell script (env install and/or the run command),
   upload it next to the synced code, and wire it to the application's
   ``initScript``/``batchScript`` parameter. With ``run`` set the job becomes a
   batch job: **UCloud terminates it when the command exits**, and the exit
   code is written to ``.ucloud/exit-<tag>`` for the queue's dependency checks.
3. Submit the resulting ``JobSpecification``.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from .catalog import Catalog
from .client import UCloudClient
from .exceptions import APIError, UCloudError
from .files import Files
from .jobs import Jobs
from .models import FileParam
from .spec import LaunchSpec, SetupSpec, SyncSpec
from .transfer import Transfer, TransferStats

#: Directories never worth syncing when there is no git index to consult.
DEFAULT_EXCLUDES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "node_modules",
        ".ucloud",
        "site",
        ".idea",
        ".vscode",
    }
)

#: Called with human-readable progress lines ("synced 12 files", ...).
EventCallback = Callable[[str], None]


def working_tree_selector(root: Path) -> Callable[[Path], bool]:
    """Return a filter for files under ``root`` worth syncing.

    In a git repository this is exactly ``git ls-files --cached --others
    --exclude-standard`` (tracked + untracked-but-not-ignored). Elsewhere it
    falls back to excluding well-known junk directories.
    """
    if (root / ".git").exists() and shutil.which("git"):
        args = ["git", "-C", str(root), "ls-files", "--cached", "--others"]
        out = subprocess.run(
            [*args, "--exclude-standard", "-z"],
            capture_output=True,
            check=True,
        )
        keep = {root / rel for rel in out.stdout.decode().split("\0") if rel}
        return lambda p: p in keep
    return lambda p: not any(part in DEFAULT_EXCLUDES for part in p.relative_to(root).parts)


class Launcher:
    """Submits a :class:`LaunchSpec` (sync + setup + create) in one call."""

    def __init__(self, client: UCloudClient) -> None:
        self._client = client
        self._jobs = Jobs(client)
        self._transfer = Transfer(client)
        self._catalog = Catalog(client)
        self._files = Files(client)

    def submit(
        self,
        spec: LaunchSpec,
        *,
        tag: str,
        on_event: EventCallback | None = None,
    ) -> str:
        """Run the launch pipeline and return the created job's id.

        ``tag`` names this launch's artifacts (setup script, log, exit file)
        inside the synced folder's ``.ucloud/`` directory — the queue passes the
        queued job's name.
        """
        emit = on_event or (lambda _msg: None)
        job_spec = spec.job.model_copy(deep=True)

        if spec.sync is not None:
            stats = self.sync_push(spec.sync, base_dir=spec.base_dir)
            emit(
                f"synced {stats.files - stats.skipped} file(s) to {spec.sync.remote}"
                f" ({stats.skipped} unchanged)"
            )
            if spec.sync.mount:
                mounts = list(job_spec.resources or [])
                if not any(isinstance(r, FileParam) and r.path == spec.sync.remote for r in mounts):
                    mounts.append(FileParam(path=spec.sync.remote))
                job_spec.resources = mounts

        if spec.setup is not None:
            assert spec.sync is not None  # enforced by LaunchSpec validation
            param_name = self._resolve_setup_param(spec)
            script_remote = f"{spec.sync.remote.rstrip('/')}/.ucloud/{tag}-setup.sh"
            script = build_setup_script(spec.setup, spec.sync, tag, base_dir=spec.base_dir)
            self._upload_text(script, script_remote)
            job_spec.parameters = {
                **job_spec.parameters,
                param_name: FileParam(path=script_remote, read_only=True),
            }
            emit(f"setup script -> {script_remote} (wired to '{param_name}')")

        job_id = self._jobs.create(job_spec)
        emit(f"submitted job {job_id}")
        return job_id

    def sync_push(self, sync: SyncSpec, *, base_dir: Path = Path()) -> TransferStats:
        """Incrementally upload the sync section's working tree."""
        local = (base_dir / sync.local).resolve()
        if not local.is_dir():
            raise UCloudError(f"[sync] local is not a directory: {local}")
        return self._transfer.upload(local, sync.remote, select=working_tree_selector(local))

    def exit_file_path(self, spec: LaunchSpec, tag: str) -> str | None:
        """Remote path of the exit-code file a batch run writes, if any."""
        if spec.sync is None or spec.setup is None or spec.setup.run is None:
            return None
        return f"{spec.sync.remote.rstrip('/')}/.ucloud/exit-{tag}"

    def run_log_path(self, spec: LaunchSpec, tag: str) -> str | None:
        """Remote path of the script's teed log, if the spec produces one.

        The whole generated script tees there (setup included), so this exists
        for ``[setup]`` specs without a ``run`` command too.
        """
        return run_log_path(spec, tag)

    def read_exit_code(self, spec: LaunchSpec, tag: str) -> int | None:
        """The run command's recorded exit code, or ``None`` if not written."""
        path = self.exit_file_path(spec, tag)
        if path is None:
            return None
        try:
            return int(self._transfer.read_bytes(path).decode().strip())
        except (UCloudError, ValueError):
            return None

    # -- internals ----------------------------------------------------------- #

    def _resolve_setup_param(self, spec: LaunchSpec) -> str:
        """Pick the app parameter to wire the setup script to, and verify it exists."""
        assert spec.setup is not None
        app = spec.job.application
        available = {p.name: p for p in self._catalog.app_parameters(app.name, app.version)}
        wanted = spec.setup.param or ("batchScript" if spec.setup.run else "initScript")
        if wanted not in available:
            file_params = [n for n, p in available.items() if p.type == "input_file"]
            raise UCloudError(
                f"App {app.name}@{app.version} has no '{wanted}' parameter. "
                f"Its file parameters are: {file_params or 'none'} — "
                "set [setup] param explicitly."
            )
        return wanted

    def _upload_text(self, content: str, remote: str) -> None:
        # The parent (e.g. the synced folder's .ucloud/) may not exist yet.
        with contextlib.suppress(APIError):
            self._files.mkdir(str(PurePosixPath(remote).parent), conflict_policy="REJECT")
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / PurePosixPath(remote).name
            local.write_text(content, encoding="utf-8")
            self._transfer.upload(local, remote)


def run_log_path(spec: LaunchSpec, tag: str) -> str | None:
    """Remote path of a launched spec's log, or ``None`` if it has no script at all.

    Shared by ``jobs logs`` and ``q logs`` so a spec's log lands in — and is read
    from — the same place no matter which command submitted it.
    """
    if spec.sync is None or spec.setup is None:
        return None
    return f"{spec.sync.remote.rstrip('/')}/.ucloud/run-{tag}.log"


def build_setup_script(
    setup: SetupSpec, sync: SyncSpec, tag: str, *, base_dir: Path = Path()
) -> str:
    """Assemble the shell script UCloud runs at job start."""
    repo = sync.in_job_path
    # Everything from here on is teed to the drive, so `ucloud q logs` shows the
    # environment build too — a fresh machine fails during setup far more often than
    # during the run, and that output is otherwise only visible in the web GUI.
    body: list[str] = []
    if setup.python == "uv":
        body += [
            "",
            "# --- python env via uv ---",
            'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"',
            "command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh",
            "uv sync",
        ]
    if setup.script is not None:
        script_path = (base_dir / setup.script).resolve()
        if not script_path.is_file():
            raise UCloudError(f"[setup] script not found: {script_path}")
        body += ["", f"# --- user script ({setup.script}) ---", script_path.read_text("utf-8")]
    if setup.run is not None:
        body += [
            "",
            "# --- run command (job ends when it exits) ---",
            f"( {setup.run} )",
            'code="$?"',
            f'echo "$code" > "$UCLOUD_DIR/exit-{tag}"',
            'exit "$code"',
        ]
    # The group runs in a subshell (it is the left side of a pipe), so `exit` above sets
    # the subshell's status and PIPESTATUS[0] carries it out. Piping rather than
    # redirecting inside keeps tee attached until it has drained, which a bare
    # `exec > >(tee …)` does not guarantee when the script exits immediately after.
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by ucloud-api — env setup and/or batch run.",
        "set -uo pipefail",
        f'REPO_DIR="{repo}"',
        'UCLOUD_DIR="$REPO_DIR/.ucloud"',
        'mkdir -p "$UCLOUD_DIR"',
        'cd "$REPO_DIR"',
        "{",
        *body,
        "",
        f'}} 2>&1 | tee "$UCLOUD_DIR/run-{tag}.log"',
        'exit "${PIPESTATUS[0]}"',
    ]
    return "\n".join(lines) + "\n"
