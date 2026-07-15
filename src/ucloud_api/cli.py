"""Command-line interface: ``ucloud <command>``."""

from __future__ import annotations

import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, NoReturn

import tomli_w
import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

from .auth import Authenticator
from .catalog import Catalog
from .client import UCloudClient
from .config import DEFAULT_BASE_URL, Credentials, credentials_path, load_credentials
from .exceptions import UCloudError
from .jobs import Jobs, SSHKeys, specification_to_spec_dict
from .models import JobSpecification, JobState
from .ssh import SSHRunner

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
app.add_typer(jobs_app, name="jobs")
app.add_typer(keys_app, name="ssh-keys")
app.add_typer(apps_app, name="apps")

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


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


@app.command()
def login(
    token: Annotated[
        str | None,
        typer.Option(
            "--token",
            help="Refresh token. If omitted, read from stdin (keeps it out of shell history).",
        ),
    ] = None,
    base_url: Annotated[
        str, typer.Option("--base-url", help="UCloud deployment URL.")
    ] = DEFAULT_BASE_URL,
) -> None:
    """Store a refresh token and verify it works.

    See the README section "Getting your refresh token" for how to extract the
    token from a browser session on any machine that has one.
    """
    if token is None:
        if sys.stdin.isatty():
            token = typer.prompt("Paste your UCloud refresh token", hide_input=True)
        else:
            token = sys.stdin.read().strip()
    token = (token or "").strip()
    if not token:
        _fail("No token provided.")

    # Verify before saving so we never persist a dud.
    auth = Authenticator(token, base_url)
    try:
        auth.access_token(force=True)
    except UCloudError as exc:
        _fail(f"Token verification failed: {exc}")
    finally:
        auth.close()

    path = Credentials(refresh_token=token, base_url=base_url).save()
    console.print(f"[green]Logged in.[/] Credentials saved to {path} (mode 0600).")


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
) -> None:
    """Submit a job described by a TOML spec file."""
    spec = _load_spec(spec_file)
    with _client() as client:
        jobs = Jobs(client)
        job_id = jobs.create(spec)
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


@app.command()
def products(
    provider: Annotated[
        str | None, typer.Option("--provider", help="Only show this provider (e.g. aau).")
    ] = None,
) -> None:
    """List compute products you can launch (id / category / provider + specs)."""
    with _client() as client:
        items = Catalog(client).products(provider=provider)
    if not items:
        console.print("[yellow]No compute products available.[/]")
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


def _format_timestamp(value: object) -> str:
    """Render UCloud's epoch-millisecond timestamps as a local datetime."""
    if not isinstance(value, (int, float)):
        return ""
    return datetime.fromtimestamp(value / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def _load_spec(spec_file: Path) -> JobSpecification:
    if not spec_file.exists():
        _fail(f"Spec file not found: {spec_file}")
    try:
        data = tomllib.loads(spec_file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _fail(f"Could not parse {spec_file}: {exc}")
    try:
        return JobSpecification.model_validate(data)
    except ValueError as exc:
        _fail(f"Invalid job specification: {exc}")


if __name__ == "__main__":  # pragma: no cover
    app()
