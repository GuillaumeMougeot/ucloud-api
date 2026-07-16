"""Command-line interface: ``ucloud <command>``."""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time
import tomllib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import tomli_w
import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from .auth import Authenticator
from .catalog import Catalog
from .client import UCloudClient
from .config import DEFAULT_BASE_URL, Credentials, credentials_path, load_credentials
from .exceptions import APIError, AuthError, UCloudError
from .files import Files
from .jobqueue import Queue, QueueRecord, QueueStatus, Scheduler
from .jobs import Jobs, SSHKeys, specification_to_spec_dict
from .launch import Launcher, working_tree_selector
from .models import JobSpecification, JobState
from .params import file as file_param
from .shell import FilesShell
from .spec import LaunchSpec, SyncSpec, load_launch_spec
from .ssh import SSHRunner
from .transfer import DEFAULT_CHUNK_SIZE, DEFAULT_CONCURRENCY, Transfer

# Load a local .env (searching from the cwd upward) so UCLOUD_* variables are
# available without exporting them by hand. Real environment variables win.
load_dotenv()

app = typer.Typer(
    name="ucloud",
    help="Launch and control UCloud (SDU eScience) GPU jobs without the web GUI.",
    no_args_is_help=True,
    add_completion=False,
)
jobs_app = typer.Typer(help="Create, inspect and connect to jobs.", no_args_is_help=True)
keys_app = typer.Typer(help="Manage SSH public keys.", no_args_is_help=True)
apps_app = typer.Typer(help="Discover applications in the catalog.", no_args_is_help=True)
files_app = typer.Typer(help="Browse UCloud drives and files.", no_args_is_help=True)
sync_app = typer.Typer(help="Sync a working tree to a UCloud drive.", no_args_is_help=True)
q_app = typer.Typer(
    help="Queue jobs with dependencies, auto-extend, and quota gating.", no_args_is_help=True
)
app.add_typer(jobs_app, name="jobs")
app.add_typer(keys_app, name="ssh-keys")
app.add_typer(apps_app, name="apps")
app.add_typer(files_app, name="files")
app.add_typer(sync_app, name="sync")
app.add_typer(q_app, name="q")

console = Console()
err_console = Console(stderr=True)


def _fail(message: str) -> NoReturn:
    err_console.print(f"[bold red]error:[/] {message}")
    raise typer.Exit(code=1)


def _client() -> UCloudClient:
    try:
        return UCloudClient()
    except UCloudError as exc:
        _fail(str(exc))


@contextlib.contextmanager
def _friendly_errors() -> Iterator[None]:
    """Turn raw file-API errors into a helpful message (esp. the misleading 403)."""
    try:
        yield
    except APIError as exc:
        if exc.status_code in (403, 404):
            _fail(
                "Path not found or not accessible. Check the path, and that your active "
                "project is correct (`ucloud projects`) — project drives need the right project."
            )
        _fail(str(exc))


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


def _try_load_credentials() -> Credentials | None:
    """Load stored credentials, or ``None`` if none are configured yet."""
    try:
        return load_credentials()
    except UCloudError:
        return None


def _verify_token(token: str, base_url: str) -> None:
    """Raise ``AuthError`` if the token cannot mint an access token."""
    auth = Authenticator(token, base_url)
    try:
        auth.access_token(force=True)
    finally:
        auth.close()


def _prompt_token() -> str:
    """Ask for a token interactively, or fail if there's no terminal to ask on."""
    if not sys.stdin.isatty():
        _fail("No token available. Pipe one in, pass --token, or run `ucloud login` in a terminal.")
    return str(typer.prompt("Paste your UCloud refresh token", hide_input=True)).strip()


@app.command()
def login(
    token: Annotated[
        str | None,
        typer.Option(
            "--token",
            help="Refresh token. If omitted, reused from storage, then stdin, then a prompt.",
        ),
    ] = None,
    base_url: Annotated[
        str | None, typer.Option("--base-url", help="UCloud deployment URL.")
    ] = None,
    project: Annotated[
        str | None,
        typer.Option("--project", help="Active project id (see `ucloud projects`)."),
    ] = None,
    reauth: Annotated[
        bool,
        typer.Option("--reauth", help="Force entering a new token even if one is stored."),
    ] = False,
) -> None:
    """Store credentials and verify they work.

    Only the settings you pass change; the rest are kept. In particular, running
    ``ucloud login --project <id>`` reuses your existing token instead of asking
    for it again. A token is obtained in this order: ``--token``, piped stdin,
    the already-stored token, then an interactive prompt. If a *reused* token has
    expired, you're prompted for a fresh one. See the README for how to get a
    token. Config resolution order and files: see the docs on Configuration.
    """
    existing = _try_load_credentials()
    resolved_base = base_url or (existing.base_url if existing else DEFAULT_BASE_URL)
    resolved_project = project if project is not None else (existing.project if existing else None)

    # An explicitly supplied token: --token, or piped stdin (never prompts here).
    provided = token
    if provided is None and not sys.stdin.isatty():
        provided = sys.stdin.read().strip() or None

    if reauth:
        good_token = provided or _prompt_token()
        try:
            _verify_token(good_token, resolved_base)
        except AuthError as exc:
            _fail(f"Token verification failed: {exc}")
    else:
        candidate = provided or (existing.refresh_token if existing else None) or _prompt_token()
        reused = provided is None and existing is not None
        try:
            _verify_token(candidate, resolved_base)
            good_token = candidate
        except AuthError as exc:
            # A token we reused has gone stale — ask for a new one, as expected.
            if reused and sys.stdin.isatty():
                err_console.print("[yellow]Your stored token is no longer valid.[/]")
                good_token = _prompt_token()
                try:
                    _verify_token(good_token, resolved_base)
                except AuthError as exc2:
                    _fail(f"Token verification failed: {exc2}")
            else:
                _fail(f"Token verification failed: {exc}")

    path = Credentials(good_token, resolved_base, resolved_project).save()
    reused_note = " (reused stored token)" if provided is None and not reauth else ""
    console.print(f"[green]Logged in.[/]{reused_note} Credentials saved to {path} (mode 0600).")
    if resolved_project:
        console.print(f"Active project: [bold]{resolved_project}[/]")
    else:
        console.print("No project set — you're in My workspace (see `ucloud projects`).")


@app.command()
def whoami() -> None:
    """Confirm the stored credentials can mint an access token."""
    try:
        creds = load_credentials()
    except UCloudError as exc:
        _fail(str(exc))
    auth = Authenticator(creds.refresh_token, creds.base_url)
    try:
        auth.access_token(force=True)
    except UCloudError as exc:
        _fail(str(exc))
    finally:
        auth.close()
    console.print(f"[green]Authenticated[/] against {creds.base_url}")
    console.print(f"Credentials file: {credentials_path()}")


# --------------------------------------------------------------------------- #
# Jobs
# --------------------------------------------------------------------------- #


@jobs_app.command("create")
def jobs_create(
    spec_file: Annotated[
        Path,
        typer.Argument(help="TOML file describing the job (see examples/pytorch.toml)."),
    ],
    wait: Annotated[
        bool, typer.Option("--wait/--no-wait", help="Wait until the job is RUNNING.")
    ] = True,
    timeout: Annotated[
        float, typer.Option("--timeout", help="Seconds to wait for RUNNING.")
    ] = 900.0,
    show_ssh: Annotated[
        bool, typer.Option("--ssh/--no-ssh", help="Print the SSH command once running.")
    ] = True,
    mount: Annotated[
        list[str] | None,
        typer.Option(
            "--mount",
            "-m",
            help="Mount a UCloud folder into the job, e.g. -m /12345/data. "
            "Append ':ro' for read-only. Repeatable.",
        ),
    ] = None,
) -> None:
    """Submit a job described by a TOML spec file.

    Runs the full launch pipeline: [sync] pushes and mounts your working tree,
    [setup] prepares the environment / runs a batch command, then the job is
    created. Plain specs without those sections submit as before.
    """
    launch = _load_launch(spec_file)
    _apply_mounts(launch.job, mount or [])
    with _client() as client:
        jobs = Jobs(client)
        try:
            job_id = Launcher(client).submit(
                launch,
                tag=launch.job.name or spec_file.stem,
                on_event=lambda m: console.print(f"[dim]{m}[/]"),
            )
        except UCloudError as exc:
            _fail(str(exc))
        console.print(f"[green]Submitted[/] job [bold]{job_id}[/]")

        if not wait:
            return

        with console.status("Waiting for job to start..."):
            try:
                jobs.wait_until_running(
                    job_id,
                    timeout=timeout,
                    on_state=lambda s: console.log(f"state -> {s.value}"),
                )
            except UCloudError as exc:
                _fail(str(exc))
        console.print(f"[green]Job {job_id} is RUNNING.[/]")

        if show_ssh:
            endpoint = jobs.ssh_endpoint(job_id)
            if endpoint:
                console.print(f"Connect with: [bold]{endpoint.command}[/]")
            else:
                console.print(
                    "[yellow]No SSH endpoint advertised yet.[/] "
                    "Ensure sshEnabled=true and a key is registered (`ucloud ssh-keys add`)."
                )


@jobs_app.command("list")
def jobs_list() -> None:
    """List your recent jobs."""
    with _client() as client:
        items = Jobs(client).browse()
    table = Table("ID", "Application", "State", "Created")
    for job in items:
        spec = job.get("specification", {})
        app_ref = spec.get("application", {})
        table.add_row(
            str(job.get("id", "?")),
            f"{app_ref.get('name', '?')}@{app_ref.get('version', '?')}",
            str(job.get("status", {}).get("state", "?")),
            _format_timestamp(job.get("createdAt")),
        )
    console.print(table)


@jobs_app.command("status")
def jobs_status(job_id: Annotated[str, typer.Argument(help="Job id.")]) -> None:
    """Show a single job's current state."""
    with _client() as client:
        state = Jobs(client).state(job_id)
    color = "green" if state == JobState.RUNNING else "yellow"
    console.print(f"Job {job_id}: [{color}]{state.value}[/]")


@jobs_app.command("show")
def jobs_show(
    job_id: Annotated[str, typer.Argument(help="Job id to export.")],
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the spec TOML here instead of stdout."),
    ] = None,
) -> None:
    """Export an existing job as a spec TOML you can re-run with `jobs create`."""
    with _client() as client:
        job = Jobs(client).retrieve(job_id)
    spec_dict = specification_to_spec_dict(job)
    toml_text = tomli_w.dumps(spec_dict)
    if output:
        output.write_text(toml_text, encoding="utf-8")
        console.print(f"[green]Wrote[/] spec for job {job_id} to {output}")
    else:
        # Plain print (no Rich markup) so the output is valid TOML to redirect.
        print(toml_text, end="")


@jobs_app.command("terminate")
def jobs_terminate(job_id: Annotated[str, typer.Argument(help="Job id.")]) -> None:
    """Terminate a running job."""
    with _client() as client:
        Jobs(client).terminate(job_id)
    console.print(f"[green]Termination requested[/] for job {job_id}")


@jobs_app.command("extend")
def jobs_extend(
    job_id: Annotated[str, typer.Argument(help="Job id.")],
    hours: Annotated[
        int, typer.Option("--hours", "-H", help="Hours to add to the allocation.")
    ] = 1,
    minutes: Annotated[int, typer.Option("--minutes", "-M", help="Minutes to add.")] = 0,
) -> None:
    """Add time to a running job (like the GUI's +1h/+8h buttons)."""
    with _client() as client:
        Jobs(client).extend(job_id, hours=hours, minutes=minutes)
    console.print(f"[green]Extended[/] job {job_id} by {hours}h{minutes:02d}m")


@jobs_app.command("ssh")
def jobs_ssh(
    job_id: Annotated[str, typer.Argument(help="Job id.")],
    command: Annotated[
        str | None,
        typer.Option("--command", "-c", help="Run this command instead of an interactive shell."),
    ] = None,
    identity_file: Annotated[
        Path | None, typer.Option("--identity", "-i", help="SSH private key to use.")
    ] = None,
) -> None:
    """SSH into a running job (interactive by default, or run one command)."""
    with _client() as client:
        endpoint = Jobs(client).ssh_endpoint(job_id)
    if endpoint is None:
        _fail(
            "No SSH endpoint for this job. Is it running with sshEnabled=true and a registered key?"
        )
    runner = SSHRunner(endpoint, identity_file=str(identity_file) if identity_file else None)
    if command:
        result = runner.run(command, capture_output=True, check=False)
        if result.stdout:
            console.print(result.stdout, end="")
        if result.stderr:
            err_console.print(result.stderr, end="")
        raise typer.Exit(code=result.returncode)
    raise typer.Exit(code=runner.interactive_shell())


@jobs_app.command("rsync")
def jobs_rsync(
    job_id: Annotated[str, typer.Argument(help="Job id.")],
    source: Annotated[str, typer.Argument(help="Local path (or in-job path with --pull).")],
    dest: Annotated[str, typer.Argument(help="In-job path, e.g. /work/repo/src/.")],
    pull: Annotated[
        bool, typer.Option("--pull", help="Reverse direction: copy from the job to here.")
    ] = False,
    delete: Annotated[
        bool, typer.Option("--delete", help="Delete extraneous files on the receiving side.")
    ] = False,
    identity_file: Annotated[
        Path | None, typer.Option("--identity", "-i", help="SSH private key to use.")
    ] = None,
) -> None:
    """rsync into (or out of) a running job over its SSH endpoint.

    Real delta transfer — ideal for iterating on code inside a live job:
    `ucloud jobs rsync 5471234 ./src/ /work/repo/src/`.
    """
    with _client() as client:
        endpoint = Jobs(client).ssh_endpoint(job_id)
    if endpoint is None:
        _fail(
            "No SSH endpoint for this job. Is it running with sshEnabled=true and a registered key?"
        )
    ssh_cmd = f"ssh -p {endpoint.port} -o StrictHostKeyChecking=accept-new"
    if identity_file:
        ssh_cmd += f" -i {identity_file}"
    remote = f"{endpoint.user}@{endpoint.host}:{dest if not pull else source}"
    src, dst = (remote, dest) if pull else (source, remote)
    args = ["rsync", "-az", "--info=progress2", "-e", ssh_cmd]
    if delete:
        args.append("--delete")
    args += [src, dst]
    try:
        raise typer.Exit(code=subprocess.run(args, check=False).returncode)
    except FileNotFoundError:
        _fail("The `rsync` command was not found on this machine.")


# --------------------------------------------------------------------------- #
# SSH keys
# --------------------------------------------------------------------------- #


@keys_app.command("add")
def keys_add(
    public_key_file: Annotated[
        Path, typer.Argument(help="Path to a public key, e.g. ~/.ssh/id_ed25519.pub")
    ],
    title: Annotated[
        str, typer.Option("--title", help="Label for the key in UCloud.")
    ] = "ucloud-api",
) -> None:
    """Register an SSH public key so SSH-enabled jobs will accept it."""
    key = public_key_file.expanduser().read_text(encoding="utf-8").strip()
    with _client() as client:
        key_id = SSHKeys(client).add(title, key)
    console.print(f"[green]Registered[/] SSH key '{title}' (id {key_id})")


@keys_app.command("list")
def keys_list() -> None:
    """List registered SSH public keys."""
    with _client() as client:
        keys = SSHKeys(client).list()
    table = Table("ID", "Title", "Fingerprint")
    for item in keys:
        spec = item.get("specification", {})
        table.add_row(
            str(item.get("id", "?")),
            str(spec.get("title", "?")),
            str(item.get("status", {}).get("fingerprint", "")),
        )
    console.print(table)


@apps_app.command("list")
def apps_list(
    category: Annotated[
        str | None,
        typer.Option("--category", "-c", help="Filter to a category (substring, e.g. 'bio')."),
    ] = None,
) -> None:
    """List every application in the catalog (grouped by category).

    The catalog is per-deployment, not per-project. Use the shown name with
    `ucloud apps show <name> <version>` (find a version via `ucloud apps search`).
    """
    with _client() as client:
        groups = Catalog(client).list_apps(category=category)
    if not groups:
        console.print("[yellow]No applications found.[/]")
        return
    table = Table("Name", "Title", "Category")
    for g in groups:
        table.add_row(g.name or "[dim]—[/]", g.title, g.category)
    console.print(table)
    console.print(f"\n[dim]{len(groups)} applications.[/]")


@apps_app.command("search")
def apps_search(
    query: Annotated[str, typer.Argument(help="Text to search for, e.g. 'pytorch'.")],
    limit: Annotated[int, typer.Option("--limit", help="Max results.")] = 25,
) -> None:
    """Search the application catalog for name/version to use in a spec."""
    with _client() as client:
        results = Catalog(client).search_apps(query, limit=limit)
    if not results:
        console.print(f"[yellow]No applications matched[/] '{query}'.")
        return
    table = Table("Name", "Version", "Title")
    for item_ in results:
        table.add_row(item_.name, item_.version, item_.title)
    console.print(table)


@apps_app.command("show")
def apps_show(
    name: Annotated[str, typer.Argument(help="Application name, e.g. pytorch-te.")],
    version: Annotated[str, typer.Argument(help="Application version, e.g. 26.05.")],
) -> None:
    """Show the parameters an application accepts (to build a spec)."""
    with _client() as client:
        parameters = Catalog(client).app_parameters(name, version)
    if not parameters:
        console.print(f"[yellow]No parameters found[/] for {name}@{version}.")
        return
    table = Table("Parameter", "Required", "Spec type", "Title")
    for p in parameters:
        table.add_row(
            p.name,
            "[red]yes[/]" if not p.optional else "no",
            p.spec_type,
            p.title,
        )
    console.print(table)
    console.print(
        "\nUse [bold]spec type[/] as the parameter's `type` in your TOML "
        "(e.g. a 'file' spec type -> a [parameters.NAME] table with type='file', path='/…')."
    )


@files_app.command("drives")
def files_drives() -> None:
    """List the drives (file collections) you can access."""
    with _client() as client, _friendly_errors():
        drives = Files(client).list_drives()
    if not drives:
        console.print("[yellow]No drives found.[/]")
        return
    table = Table("Path", "Title", "Provider")
    for d in drives:
        table.add_row(d.path, d.title, d.provider)
    console.print(table)
    console.print("\nBrowse one with: [bold]ucloud files ls <path>[/]")


@files_app.command("ls")
def files_ls(
    path: Annotated[str, typer.Argument(help="Path to list, e.g. /12345/project.")],
) -> None:
    """List the contents of a UCloud folder."""
    with _client() as client, _friendly_errors():
        entries = Files(client).list_path(path)
    if not entries:
        console.print(f"[yellow]Empty or not found:[/] {path}")
        return
    table = Table("Type", "Name", "Size", "Modified")
    for e in entries:
        table.add_row(
            "dir" if e.is_dir else "file",
            e.name + ("/" if e.is_dir else ""),
            _format_size(e.size) if not e.is_dir else "",
            _format_timestamp(e.modified_at),
        )
    console.print(table)
    console.print(f"\nMount a folder into a job with: [bold]jobs create spec.toml -m {path}[/]")


def _transfer_progress() -> Progress:
    return Progress(
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )


@files_app.command("upload")
def files_upload(
    local: Annotated[Path, typer.Argument(help="Local file or directory to upload.")],
    remote: Annotated[str, typer.Argument(help="Destination path, e.g. /959294/project.")],
    concurrency: Annotated[
        int, typer.Option("--concurrency", "-j", help="Files transferred in parallel.")
    ] = DEFAULT_CONCURRENCY,
    chunk_mb: Annotated[
        int, typer.Option("--chunk-mb", help="Upload chunk size in MiB.")
    ] = DEFAULT_CHUNK_SIZE // (1024 * 1024),
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite/--no-overwrite", help="Replace vs. rename on conflict."),
    ] = True,
) -> None:
    """Upload a file or directory to UCloud (many files in parallel)."""
    with _client() as client, _friendly_errors(), _transfer_progress() as prog:
        task = prog.add_task("upload", total=None, start=False)

        def on_start(count: int, total: int) -> None:
            prog.update(task, total=total, description=f"↑ {count} file(s)")
            prog.start_task(task)

        stats = Transfer(client).upload(
            local,
            remote,
            concurrency=concurrency,
            chunk_size=chunk_mb * 1024 * 1024,
            overwrite=overwrite,
            progress=lambda n: prog.advance(task, n),
            on_start=on_start,
        )
    console.print(
        f"[green]Uploaded[/] {stats.files} file(s), {_format_size(stats.total_bytes)} → {remote}"
    )


@files_app.command("download")
def files_download(
    remote: Annotated[str, typer.Argument(help="UCloud file or directory, e.g. /959294/project.")],
    local: Annotated[Path, typer.Argument(help="Local destination.")],
    concurrency: Annotated[
        int, typer.Option("--concurrency", "-j", help="Files transferred in parallel.")
    ] = DEFAULT_CONCURRENCY,
) -> None:
    """Download a file or directory from UCloud (many files in parallel)."""
    with _client() as client, _friendly_errors(), _transfer_progress() as prog:
        task = prog.add_task("download", total=None, start=False)

        def on_start(count: int, total: int) -> None:
            prog.update(task, total=total or None, description=f"↓ {count} file(s)")
            prog.start_task(task)

        stats = Transfer(client).download(
            remote,
            local,
            concurrency=concurrency,
            progress=lambda n: prog.advance(task, n),
            on_start=on_start,
        )
    console.print(
        f"[green]Downloaded[/] {stats.files} file(s), {_format_size(stats.total_bytes)} → {local}"
    )


@files_app.command("mkdir")
def files_mkdir(
    path: Annotated[str, typer.Argument(help="Folder to create, e.g. /959294/newdir.")],
) -> None:
    """Create a folder on UCloud."""
    with _client() as client, _friendly_errors():
        Files(client).mkdir(path)
    console.print(f"[green]Created[/] {path}")


@files_app.command("rm")
def files_rm(
    path: Annotated[str, typer.Argument(help="File or folder to move to trash.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation.")] = False,
) -> None:
    """Move a file or folder to the UCloud trash."""
    if not yes and not typer.confirm(f"Move {path} to trash?"):
        raise typer.Abort
    with _client() as client, _friendly_errors():
        Files(client).trash(path)
    console.print(f"[green]Moved to trash:[/] {path}")


@files_app.command("shell")
def files_shell(
    start: Annotated[
        str, typer.Argument(help="Directory to start in (default: root, which lists drives).")
    ] = "/",
) -> None:
    """Open an interactive browser: cd / ls / get / put with tab-completion."""
    with _client() as client:
        FilesShell(client, start=start).run()


@app.command()
def projects() -> None:
    """List the projects you belong to (use one with `login --project <id>`)."""
    with _client() as client:
        data = client.get("/api/projects/v2/browse", params={"itemsPerPage": 250})
        active = client.project
    items = data.get("items", []) if isinstance(data, dict) else []
    table = Table("Active", "ID", "Title")
    # "My workspace" (personal) is the default when no project is set — it is not
    # returned by the projects API, so we add it explicitly to make it visible.
    table.add_row(
        "[green]*[/]" if not active else "",
        "[dim](none)[/]",
        "My workspace (personal — usually no GPU/storage allocation)",
    )
    for it in items:
        pid = str(it.get("id", "?"))
        table.add_row(
            "[green]*[/]" if pid == active else "",
            pid,
            str(it.get("specification", {}).get("title", "")),
        )
    console.print(table)
    console.print(
        "\nSwitch project with: [bold]ucloud login --project <ID>[/] "
        "(or set UCLOUD_PROJECT in .env). Leave unset to stay in My workspace."
    )


@app.command()
def products(
    provider: Annotated[
        str | None, typer.Option("--provider", help="Only show this provider (e.g. aau).")
    ] = None,
    show_all: Annotated[
        bool,
        typer.Option("--all", help="Show the whole deployment catalog, not just what you can use."),
    ] = False,
) -> None:
    """List compute products usable in the active workspace (id / category / specs).

    By default only products whose category has remaining quota in the active
    workspace are shown (the catalog itself is the same for everyone). Use
    `--all` for the full deployment catalog, and `ucloud quota` for the numbers.
    """
    with _client() as client:
        items = Catalog(client).products(provider=provider, usable_only=not show_all)
    if not items:
        console.print(
            "[yellow]No usable compute products in this workspace.[/] "
            "Check `ucloud quota`, switch project (`ucloud projects`), or pass --all."
        )
        return
    table = Table("Provider", "ID", "Category", "vCPU", "Mem (GB)", "GPU")
    for p in items:
        table.add_row(
            p.provider,
            p.id,
            p.category,
            str(p.cpu or "-"),
            str(p.memory_gb or "-"),
            str(p.gpu or "-"),
        )
    console.print(table)
    if not show_all:
        console.print("[dim]Filtered to categories with remaining quota; --all for everything.[/]")


@app.command()
def quota() -> None:
    """Show the active workspace's allocations (what `products` filters on)."""
    with _client() as client:
        wallets = Catalog(client).wallets()
        active = client.project
    if not wallets:
        console.print("[yellow]No allocations in this workspace.[/]")
        return
    table = Table("Type", "Category", "Provider", "Used", "Quota", "Left", "Unit")
    for w in sorted(wallets, key=lambda w: (not w.usable, w.product_type, w.category)):
        style = "" if w.usable else "dim"
        table.add_row(
            w.product_type.capitalize(),
            w.category,
            w.provider,
            _format_quantity(w.usage),
            _format_quantity(w.quota),
            _format_quantity(w.max_usable) if w.usable else "[red]0[/]",
            w.unit,
            style=style,
        )
    console.print(table)
    where = f"project {active}" if active else "My workspace (personal)"
    console.print(f"[dim]Workspace: {where}. Switch with `ucloud login --project <id>`.[/]")


def _format_quantity(value: float) -> str:
    return f"{value:,.0f}" if value == int(value) else f"{value:,.1f}"


def _format_timestamp(value: object) -> str:
    """Render UCloud's epoch-millisecond timestamps as a local datetime."""
    if not isinstance(value, (int, float)):
        return ""
    return datetime.fromtimestamp(value / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def _format_size(value: int | None) -> str:
    """Human-readable byte size."""
    if value is None:
        return ""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #


@sync_app.command("push")
def sync_push(
    spec_or_local: Annotated[
        str,
        typer.Argument(
            help="A spec TOML with a [sync] section, or a local directory "
            "(then pass the remote folder as the second argument)."
        ),
    ],
    remote: Annotated[
        str | None,
        typer.Argument(help="Remote folder (only with a local directory), e.g. /12345/repos/x."),
    ] = None,
) -> None:
    """Incrementally push a working tree to a UCloud drive folder.

    Only new/changed files travel (the server skips files with matching size
    and mtime). In a git repo, `.gitignore` is respected; elsewhere well-known
    junk directories are excluded. Deletions are not propagated.
    """
    if remote is None:
        launch = _load_launch(Path(spec_or_local))
        if launch.sync is None:
            _fail(f"{spec_or_local} has no [sync] section (or pass <local> <remote>).")
        sync = launch.sync
        base_dir = launch.base_dir
    else:
        sync = SyncSpec(local=spec_or_local, remote=remote)
        base_dir = Path()
    local = (base_dir / sync.local).resolve()
    if not local.is_dir():
        _fail(f"Not a directory: {local}")
    with _client() as client, _friendly_errors(), _transfer_progress() as prog:
        task = prog.add_task("sync", total=None, start=False)

        def on_start(count: int, total: int) -> None:
            prog.update(task, total=total, description=f"↑ {count} file(s)")
            prog.start_task(task)

        stats = Transfer(client).upload(
            local,
            sync.remote,
            progress=lambda n: prog.advance(task, n),
            on_start=on_start,
            select=working_tree_selector(local),
        )
    console.print(
        f"[green]synced[/] {stats.files - stats.skipped} file(s) -> {sync.remote} "
        f"({stats.skipped} unchanged)"
    )


# --------------------------------------------------------------------------- #
# Queue
# --------------------------------------------------------------------------- #


@q_app.command("submit")
def q_submit(
    spec_file: Annotated[Path, typer.Argument(help="Spec TOML (see `jobs create`).")],
    name: Annotated[
        str | None,
        typer.Option("--name", "-n", help="Queue name (default: the spec's job name/file stem)."),
    ] = None,
    after: Annotated[
        list[str] | None,
        typer.Option(
            "--after",
            "-a",
            help="Run only after this queued job succeeds (repeatable).",
        ),
    ] = None,
    tick_now: Annotated[
        bool, typer.Option("--tick/--no-tick", help="Advance the queue immediately.")
    ] = True,
) -> None:
    """Queue a job. Independent jobs launch as soon as quota allows; jobs with
    --after wait for their dependencies (code is synced at launch time)."""
    launch = _load_launch(spec_file)  # validate now, fail early
    data = tomllib.loads(spec_file.read_text(encoding="utf-8"))
    record = QueueRecord(
        name=name or launch.job.name or spec_file.stem,
        spec=data,
        base_dir=str(spec_file.resolve().parent),
        after=list(after or []),
    )
    try:
        Queue().add(record)
    except UCloudError as exc:
        _fail(str(exc))
    deps = f" (after {', '.join(record.after)})" if record.after else ""
    console.print(f"[green]queued[/] {record.name}{deps}")
    if tick_now:
        _run_tick()


@q_app.command("ls")
def q_ls() -> None:
    """List queued/submitted/running/finished jobs."""
    records = Queue().all()
    if not records:
        console.print("[dim]queue is empty[/]")
        return
    colors = {
        QueueStatus.QUEUED: "yellow",
        QueueStatus.SUBMITTED: "cyan",
        QueueStatus.RUNNING: "green",
        QueueStatus.DONE: "blue",
        QueueStatus.FAILED: "red",
        QueueStatus.BLOCKED: "red",
        QueueStatus.CANCELLED: "dim",
    }
    table = Table("Name", "Status", "Job", "After", "Info")
    for r in records:
        table.add_row(
            r.name,
            f"[{colors[r.status]}]{r.status.value}[/]",
            r.job_id or "",
            ", ".join(r.after),
            r.message,
        )
    console.print(table)


@q_app.command("tick")
def q_tick() -> None:
    """Advance the queue once: reconcile states, extend low-time jobs, submit
    eligible ones. Safe to run from cron."""
    _run_tick()


@q_app.command("daemon")
def q_daemon(
    interval: Annotated[float, typer.Option("--interval", help="Seconds between ticks.")] = 60.0,
    until_idle: Annotated[
        bool,
        typer.Option("--until-idle", help="Exit when no queued or active jobs remain."),
    ] = False,
) -> None:
    """Tick in a loop. Stopping it never harms running jobs — they are normal
    UCloud jobs; queued ones wait on disk until something ticks again."""
    console.print(f"[dim]q daemon: ticking every {interval:g}s (Ctrl-C to stop)[/]")
    queue = Queue()
    try:
        while True:
            _run_tick(queue)
            if until_idle and not any(not r.status.is_terminal for r in queue.all()):
                console.print("[dim]queue idle — exiting[/]")
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]daemon stopped; running jobs continue on UCloud[/]")


@q_app.command("rm")
def q_rm(
    name: Annotated[str, typer.Argument(help="Queue name (see `q ls`).")],
    terminate: Annotated[
        bool, typer.Option("--terminate", help="Also terminate the UCloud job if active.")
    ] = False,
) -> None:
    """Remove a job from the queue (dependents become BLOCKED)."""
    queue = Queue()
    record = queue.get(name)
    if record is None:
        _fail(f"No queued job named {name!r}.")
    if record.status in (QueueStatus.SUBMITTED, QueueStatus.RUNNING):
        if not terminate:
            _fail(
                f"{name} is {record.status.value} as job {record.job_id}. "
                "Pass --terminate to also stop it, or terminate it yourself first."
            )
        with _client() as client:
            Jobs(client).terminate(record.job_id or "")
        console.print(f"[yellow]terminated[/] job {record.job_id}")
    queue.delete(name)
    console.print(f"[green]removed[/] {name}")


@q_app.command("clear")
def q_clear() -> None:
    """Remove all finished (DONE/FAILED/BLOCKED/CANCELLED) records."""
    queue = Queue()
    removed = 0
    for r in queue.all():
        if r.status.is_terminal:
            queue.delete(r.name)
            removed += 1
    console.print(f"[green]removed[/] {removed} finished record(s)")


@q_app.command("logs")
def q_logs(name: Annotated[str, typer.Argument(help="Queue name (see `q ls`).")]) -> None:
    """Print a batch run's log (written to the synced folder's .ucloud/ dir)."""
    record = Queue().get(name)
    if record is None:
        _fail(f"No queued job named {name!r}.")
    launch = record.launch_spec()
    if launch.sync is None or launch.setup is None or launch.setup.run is None:
        _fail(f"{name} has no batch run ([sync] + [setup] run), so there is no log.")
    log_path = f"{launch.sync.remote.rstrip('/')}/.ucloud/run-{name}.log"
    with _client() as client, _friendly_errors():
        content = Transfer(client).read_bytes(log_path)
    print(content.decode(errors="replace"), end="")


def _run_tick(queue: Queue | None = None) -> None:
    with _client() as client:
        events = Scheduler(client, queue).tick()
    stamp = datetime.now(tz=UTC).strftime("%H:%M:%S")
    for event in events:
        console.print(f"[dim]{stamp}[/] {event}")


def _apply_mounts(spec: JobSpecification, mounts: list[str]) -> None:
    """Append ``--mount`` folders to the spec as file resources.

    Each mount is ``/driveId/path`` with an optional ``:ro`` suffix for
    read-only. Folders are mounted into a job via its ``resources`` list.
    """
    if not mounts:
        return
    resources = list(spec.resources or [])
    for raw in mounts:
        path, _, mode = raw.partition(":")
        read_only = mode.strip().lower() in {"ro", "readonly", "read-only"}
        if not path.startswith("/"):
            _fail(f"Mount path must be absolute (e.g. /12345/data): {raw!r}")
        resources.append(file_param(path, read_only=read_only))
    spec.resources = resources


def _load_launch(spec_file: Path) -> LaunchSpec:
    try:
        return load_launch_spec(spec_file)
    except UCloudError as exc:
        _fail(str(exc))


if __name__ == "__main__":  # pragma: no cover
    app()
