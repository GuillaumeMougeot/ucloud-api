"""Project detection and the choices `ucloud init` makes."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from ucloud_api.catalog import AppDetails, AppParameter, AppSummary, ComputeProductInfo
from ucloud_api.exceptions import UCloudError
from ucloud_api.files import Drive
from ucloud_api.scaffold import (
    Plan,
    detect_project,
    pick_app,
    pick_drive,
    pick_product,
    render_job_toml,
    render_setup_sh,
    write_files,
)
from ucloud_api.spec import parse_launch_spec


def _product(pid: str, cpu: int, gpu: int, mem: int = 100) -> ComputeProductInfo:
    return ComputeProductInfo(
        id=pid, category="cat", provider="ucloud", cpu=cpu, memory_gb=mem, gpu=gpu, description=""
    )


def _param(name: str, ptype: str = "input_file") -> AppParameter:
    return AppParameter(
        name=name, type=ptype, optional=True, title="", description="", default=None
    )


def _details(*, params: list[AppParameter] | None = None, ssh: str | None = None) -> AppDetails:
    return AppDetails(
        name="pytorch-te",
        version="26.05",
        parameters=params if params is not None else [_param("batchScript")],
        ssh_mode=ssh,
    )


# -- project detection ------------------------------------------------------- #


def test_detect_project_reads_name_from_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "LepiNet"\n', encoding="utf-8")
    info = detect_project(tmp_path)
    assert info.name == "lepinet"  # slugged: it becomes a drive folder and a job name
    assert not info.uses_uv


def test_detect_project_falls_back_to_directory_name(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("torch\n", encoding="utf-8")
    assert detect_project(tmp_path).name == tmp_path.resolve().name.lower()


def test_detect_project_survives_broken_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("this is not [ toml", encoding="utf-8")
    assert detect_project(tmp_path).name  # falls back rather than raising


def test_detect_project_spots_uv(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    assert detect_project(tmp_path).uses_uv


def test_detect_project_refuses_non_python(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text("package main", encoding="utf-8")
    with pytest.raises(UCloudError, match="does not look like a Python project"):
        detect_project(tmp_path)


# -- choices ----------------------------------------------------------------- #


def test_pick_product_prefers_a_single_gpu_over_a_bigger_node() -> None:
    products = [_product("cpu-8", 8, 0), _product("gpu-8", 384, 8), _product("gpu-1", 48, 1)]
    assert pick_product(products).id == "gpu-1"


def test_pick_product_takes_the_fattest_single_gpu_machine() -> None:
    products = [_product("mig", 6, 1), _product("full", 48, 1)]
    assert pick_product(products).id == "full"


def test_pick_product_falls_back_to_cpu_without_gpus() -> None:
    assert pick_product([_product("cpu-4", 4, 0), _product("cpu-64", 64, 0)]).id == "cpu-64"


def test_pick_product_rejects_a_product_without_quota() -> None:
    with pytest.raises(UCloudError, match="not available"):
        pick_product([_product("gpu-1", 48, 1)], "gpu-nope")


def test_pick_app_prefers_newest_pytorch() -> None:
    apps = [
        AppSummary(name="rstudio", version="4", title="", description=""),
        AppSummary(name="pytorch-te", version="25.01", title="", description=""),
        AppSummary(name="pytorch-te", version="26.05", title="", description=""),
    ]
    assert (pick_app(apps).name, pick_app(apps).version) == ("pytorch-te", "26.05")


def test_pick_drive_matches_bare_and_slashed_ids() -> None:
    drives = [
        Drive(id="1", title="a", provider="ucloud"),
        Drive(id="12347837", title="b", provider="ucloud"),
    ]
    assert pick_drive(drives, "12347837").id == "12347837"
    assert pick_drive(drives, "/12347837").id == "12347837"
    assert pick_drive(drives).id == "1"  # default: the first


def test_pick_drive_rejects_unknown() -> None:
    with pytest.raises(UCloudError, match="not found"):
        pick_drive([Drive(id="1", title="a", provider="ucloud")], "9")


# -- app details ------------------------------------------------------------- #


def test_script_param_prefers_batch_script() -> None:
    d = _details(params=[_param("initScript"), _param("batchScript")])
    assert d.script_param == "batchScript"


def test_script_param_none_when_app_takes_no_script() -> None:
    assert _details(params=[_param("someText", "text")]).script_param is None


def test_supports_ssh_reads_mode() -> None:
    assert _details(ssh="OPTIONAL").supports_ssh
    assert not _details(ssh="DISABLED").supports_ssh
    assert not _details(ssh=None).supports_ssh  # pytorch-te: no ssh block at all


# -- rendering --------------------------------------------------------------- #


def _plan(tmp_path: Path, *, ssh: str | None = None) -> Plan:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    return Plan(
        project=detect_project(tmp_path),
        drive=Drive(id="12347837", title="Member Files", provider="ucloud"),
        product=_product("gpu-nvidia-b200-1-gpu", 48, 1, 288),
        app=AppSummary(name="pytorch-te", version="26.05", title="", description=""),
        details=_details(ssh=ssh),
        remote="/12347837/repos/demo",
    )


def test_rendered_spec_has_real_values_not_placeholders(tmp_path: Path) -> None:
    toml = render_job_toml(_plan(tmp_path))
    assert "REPLACE_ME" not in toml
    assert 'id = "gpu-nvidia-b200-1-gpu"' in toml
    assert 'remote = "/12347837/repos/demo"' in toml
    assert 'version = "26.05"' in toml


def test_rendered_spec_parses_and_matches_the_launch_spec_schema(tmp_path: Path) -> None:
    """The whole point is that it runs — so it must satisfy the real spec loader."""
    data = tomllib.loads(render_job_toml(_plan(tmp_path)))
    spec = parse_launch_spec(data, base_dir=tmp_path)
    assert spec.job.product.id == "gpu-nvidia-b200-1-gpu"
    assert spec.sync is not None and spec.sync.in_job_path == "/work/demo"
    assert spec.setup is not None and spec.setup.run == "python train.py"


def test_rendered_spec_omits_ssh_when_the_app_rejects_it(tmp_path: Path) -> None:
    assert "ssh_enabled = true" not in render_job_toml(_plan(tmp_path, ssh=None))
    assert "ssh_enabled = true" in render_job_toml(_plan(tmp_path, ssh="OPTIONAL"))


def test_rendered_setup_builds_the_venv_off_the_network_mount(tmp_path: Path) -> None:
    """A venv on /work is slow and eats drive quota — it belongs on the job's own disk."""
    sh = render_setup_sh(_plan(tmp_path))
    assert "uv venv /tmp/venv" in sh
    assert "UV_CACHE_DIR=/tmp/uv-cache" in sh
    assert "uv venv /work" not in sh


# -- writing ----------------------------------------------------------------- #


def test_write_files_creates_both(tmp_path: Path) -> None:
    written = write_files(_plan(tmp_path), tmp_path / "ucloud")
    assert [p.name for p in written] == ["job.toml", "setup.sh"]
    assert (tmp_path / "ucloud" / "job.toml").is_file()


def test_write_files_refuses_to_clobber(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    write_files(plan, tmp_path / "ucloud")
    (tmp_path / "ucloud" / "setup.sh").write_text("my edits", encoding="utf-8")
    with pytest.raises(UCloudError, match="--force"):
        write_files(plan, tmp_path / "ucloud")
    assert (tmp_path / "ucloud" / "setup.sh").read_text() == "my edits"

    write_files(plan, tmp_path / "ucloud", force=True)
    assert (tmp_path / "ucloud" / "setup.sh").read_text() != "my edits"
