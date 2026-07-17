"""Scaffold a job spec and setup script for the project in the current directory.

Backs ``ucloud init``. The point is not the folder — a template full of REPLACE_ME
is no better than ``examples/pytorch.toml`` — it is that everything a spec needs
(drive path, product id, application version, which script parameter the app
takes, whether it will accept SSH) is discoverable from the API, so init can
write a spec that runs.

Deliberately narrow: Python projects, uv, batch training. Anything else is told
to read the docs instead of being handed a template that cannot work.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .catalog import AppDetails, AppSummary, ComputeProductInfo
from .exceptions import UCloudError
from .files import Drive

#: Files that make a directory a Python project worth scaffolding for.
_PYTHON_MARKERS = ("pyproject.toml", "requirements.txt", "setup.py", "setup.cfg")
_SAFE_NAME = re.compile(r"[^a-z0-9._-]+")


@dataclass(slots=True)
class ProjectInfo:
    """What we could work out about the local working tree."""

    root: Path
    name: str
    uses_uv: bool
    is_git: bool


@dataclass(slots=True)
class Plan:
    """Everything needed to render the files, resolved against the live API."""

    project: ProjectInfo
    drive: Drive
    product: ComputeProductInfo
    app: AppSummary
    details: AppDetails
    remote: str

    @property
    def in_job_path(self) -> str:
        return f"/work/{self.remote.rstrip('/').rsplit('/', 1)[-1]}"


def detect_project(root: Path) -> ProjectInfo:
    """Inspect ``root``, or refuse if it is not a Python project."""
    if not any((root / m).is_file() for m in _PYTHON_MARKERS):
        raise UCloudError(
            f"{root} does not look like a Python project (no "
            f"{', '.join(_PYTHON_MARKERS)}).\n"
            "ucloud init only scaffolds Python + uv batch jobs; for anything else "
            "copy examples/pytorch.toml and see docs/queue-and-batch.md."
        )
    name = _project_name(root)
    pyproject = root / "pyproject.toml"
    uses_uv = (root / "uv.lock").is_file() or (
        pyproject.is_file() and "[tool.uv" in pyproject.read_text("utf-8")
    )
    return ProjectInfo(root=root, name=name, uses_uv=uses_uv, is_git=(root / ".git").exists())


def _project_name(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text("utf-8"))
        except tomllib.TOMLDecodeError:
            data = {}
        name = ((data.get("project") or {}).get("name")) if isinstance(data, dict) else None
        if isinstance(name, str) and name.strip():
            return _slug(name)
    return _slug(root.resolve().name)


def _slug(value: str) -> str:
    """A name safe for a drive folder and a job name."""
    return _SAFE_NAME.sub("-", value.strip().lower()).strip("-") or "project"


def pick_drive(drives: list[Drive], requested: str | None = None) -> Drive:
    if not drives:
        raise UCloudError("no drives found — check `ucloud projects` / `ucloud login --project`.")
    if requested is None:
        return drives[0]
    wanted = requested.strip().lstrip("/")
    for drive in drives:
        if drive.id == wanted or drive.title == requested:
            return drive
    known = ", ".join(f"/{d.id}" for d in drives)
    raise UCloudError(f"drive {requested!r} not found. Available: {known}")


def pick_product(
    products: list[ComputeProductInfo], requested: str | None = None
) -> ComputeProductInfo:
    """Choose a product: the caller's, else a single-GPU machine, else the biggest CPU.

    ``products`` is expected to be quota-filtered already. One GPU rather than the
    largest multi-GPU node: a scaffold should start something a person can afford
    to leave running by mistake.
    """
    if requested is not None:
        for product in products:
            if product.id == requested:
                return product
        raise UCloudError(
            f"product {requested!r} is not available to this workspace — see `ucloud products`."
        )
    if not products:
        raise UCloudError("no products with remaining quota — see `ucloud quota`.")
    single_gpu = [p for p in products if (p.gpu or 0) == 1]
    if single_gpu:
        return max(single_gpu, key=lambda p: (p.cpu or 0, p.memory_gb or 0))
    gpus = [p for p in products if (p.gpu or 0) > 0]
    if gpus:
        return min(gpus, key=lambda p: p.gpu or 0)
    return max(products, key=lambda p: (p.cpu or 0, p.memory_gb or 0))


def pick_app(apps: list[AppSummary], requested: str | None = None) -> AppSummary:
    """Choose an application: the caller's, else the newest PyTorch."""
    if not apps:
        raise UCloudError("no applications found in the catalog.")
    if requested is not None:
        named = [a for a in apps if a.name == requested]
        if not named:
            raise UCloudError(f"application {requested!r} not found — see `ucloud apps list`.")
        return max(named, key=lambda a: a.version)
    torch = [a for a in apps if "pytorch" in a.name.lower()]
    if torch:
        return max(torch, key=lambda a: a.version)
    return apps[0]


def render_job_toml(plan: Plan) -> str:
    """The spec file. Values are real, not placeholders — that is the whole point."""
    p, prod, app, det = plan.project, plan.product, plan.app, plan.details
    gpu = f", {prod.gpu} GPU" if prod.gpu else ""
    ssh_line = (
        "ssh_enabled = true            # this app accepts SSH: `ucloud jobs ssh <id>`"
        if det.supports_ssh
        else f"# No ssh_enabled: {app.name} does not support SSH and would reject the job.\n"
        "# Watch the run with `ucloud q logs` instead."
    )
    batch = det.script_param == "batchScript"
    run_note = (
        "# `run` makes this a batch job: UCloud ends the job when the command exits, so a\n"
        "# finished (or crashed) run never leaves the machine idle."
        if batch
        else "# This app takes an initScript, not a batchScript: the job stays up after setup.\n"
        "# Remove `run` and use `ucloud jobs ssh` if you want an interactive session."
    )
    return f"""\
# Generated by `ucloud init`. Values below were read from your workspace, so this
# should run as-is once `run` points at your training command.
#   ucloud q submit ucloud/job.toml --name {p.name}
#   ucloud q logs {p.name}
# Docs: https://guillaumemougeot.github.io/ucloud-api/queue-and-batch/

name = "{p.name}"
replicas = 1
{ssh_line}

[application]
name = "{app.name}"
version = "{app.version}"        # `ucloud apps search {app.name}` for other versions

[product]
# {prod.cpu} vCPU, {prod.memory_gb} GB{gpu}. Others: `ucloud products`.
id = "{prod.id}"
category = "{prod.category}"
provider = "{prod.provider}"

[time_allocation]
hours = 4                        # an honest estimate; [schedule] covers the overrun

[sync]
# Pushes this working tree to the drive and mounts it at {plan.in_job_path}.
# Incremental and .gitignore-aware, so data/ and .venv never travel.
local = ".."
remote = "{plan.remote}"

[setup]
script = "setup.sh"              # builds the environment inside the job
{run_note}
run = "python train.py"          # <-- your training command

[schedule]
auto_extend = "1h"               # +1h whenever <15 min remain...
max_time = "24h"                 # ...never past this total

# Mount a dataset that already lives on the drive (appears at /work/<folder>):
# [[resources]]
# type = "file"
# path = "{plan.drive.path}/datasets/my-dataset"
# read_only = true
"""


def render_setup_sh(plan: Plan) -> str:
    """The environment script, embedded into the job's script by `jobs create`/`q`."""
    repo = plan.in_job_path
    install = (
        "uv pip install ."
        if not plan.project.uses_uv
        else "uv pip install .   # or `uv sync --active` if you keep a lock in the job"
    )
    scm = (
        ""
        if not (plan.project.root / "pyproject.toml").is_file()
        else """
# If your build backend derives a version from git (setuptools-scm, hatch-vcs), it will
# fail here: the sync ships the working tree without .git. Set a fallback in pyproject,
# or uncomment:
# export SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0
"""
    )
    return f"""\
# Generated by `ucloud init`. Runs inside the job, with cwd = {repo}.
# Embedded into the batch script, so its output reaches `ucloud q logs` too.

set -x

# The image has no uv.
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh

# Build the venv on the job's local disk. {repo} is network storage: putting a
# multi-GB torch install there is slow and burns drive quota.
export UV_CACHE_DIR=/tmp/uv-cache
uv venv /tmp/venv
# shellcheck disable=SC1091
source /tmp/venv/bin/activate
{scm}
{install}

# Fail loudly if the GPU is missing rather than training on CPU for hours. Both
# failures below are worth catching here: they are cheap now and expensive later.
python - <<'PY'
try:
    import torch
except ModuleNotFoundError:
    raise SystemExit(
        "torch is not installed. The setup above installs THIS PROJECT's declared "
        "dependencies -- add torch to pyproject.toml/requirements.txt, or install it "
        "explicitly in ucloud/setup.sh."
    )
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
if not torch.cuda.is_available():
    raise SystemExit("no CUDA device visible to torch")
print("device", torch.cuda.get_device_name(0))
PY

# Reading many small files from the mount is latency-bound (~90 ms each). If your
# loader reads images from /work, raise its worker count well past the core count --
# the workers sit blocked on the network, not on CPU. With PyTorch, that is
# DataLoader(num_workers=256); frameworks often default to 16 and starve the GPU.
"""


def write_files(plan: Plan, target: Path, *, force: bool = False) -> list[Path]:
    """Write ucloud/job.toml and ucloud/setup.sh; never clobber without ``force``."""
    files = {target / "job.toml": render_job_toml(plan), target / "setup.sh": render_setup_sh(plan)}
    existing = [p for p in files if p.exists()]
    if existing and not force:
        names = ", ".join(str(p) for p in existing)
        raise UCloudError(f"{names} already exists — pass --force to overwrite.")
    target.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    return sorted(files)
