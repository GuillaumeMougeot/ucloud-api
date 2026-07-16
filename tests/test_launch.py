"""Launch pipeline: setup-script generation, tree selection, param wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from ucloud_api.exceptions import UCloudError
from ucloud_api.launch import Launcher, build_setup_script, working_tree_selector
from ucloud_api.spec import SetupSpec, SyncSpec, parse_launch_spec

_SYNC = SyncSpec(remote="/123/repos/proj")


def test_setup_script_uv_env_block() -> None:
    script = build_setup_script(SetupSpec(python="uv"), _SYNC, "t1")
    assert 'REPO_DIR="/work/proj"' in script
    assert "astral.sh/uv/install.sh" in script
    assert "uv sync" in script
    assert "exit-t1" not in script  # no run -> no exit-code recording


def test_setup_script_run_records_exit_code_and_log() -> None:
    script = build_setup_script(SetupSpec(run="python train.py"), _SYNC, "t2")
    assert "( python train.py )" in script
    assert 'echo "$code" > "$UCLOUD_DIR/exit-t2"' in script
    # The run's status has to survive the tee pipeline to become the job's status.
    assert script.rstrip().endswith('exit "${PIPESTATUS[0]}"')


def test_setup_script_tees_setup_output_not_just_the_run() -> None:
    """Environment build output must reach the drive: that is where jobs usually fail."""
    script = build_setup_script(SetupSpec(python="uv", run="python train.py"), _SYNC, "t2")
    assert '} 2>&1 | tee "$UCLOUD_DIR/run-t2.log"' in script
    uv_line = script.index("uv sync")
    assert uv_line < script.index("| tee"), "uv sync must run inside the teed block"


def test_setup_script_without_run_still_tees() -> None:
    script = build_setup_script(SetupSpec(python="uv"), _SYNC, "t4")
    assert '} 2>&1 | tee "$UCLOUD_DIR/run-t4.log"' in script


def test_setup_script_embeds_user_script(tmp_path: Path) -> None:
    (tmp_path / "setup.sh").write_text("module load cuda\n", encoding="utf-8")
    script = build_setup_script(SetupSpec(script="setup.sh"), _SYNC, "t3", base_dir=tmp_path)
    assert "module load cuda" in script


def test_setup_script_missing_user_script(tmp_path: Path) -> None:
    with pytest.raises(UCloudError, match="script not found"):
        build_setup_script(SetupSpec(script="nope.sh"), _SYNC, "t", base_dir=tmp_path)


def test_working_tree_selector_fallback_excludes_junk(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x", encoding="utf-8")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "big.so").write_text("x", encoding="utf-8")
    select = working_tree_selector(tmp_path)
    assert select(tmp_path / "src" / "main.py")
    assert not select(tmp_path / ".venv" / "lib" / "big.so")


def test_working_tree_selector_honours_gitignore(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "keep.py").write_text("x", encoding="utf-8")
    (tmp_path / "secret.env").write_text("x", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("secret.env\n", encoding="utf-8")
    select = working_tree_selector(tmp_path)
    assert select(tmp_path / "keep.py")
    assert not select(tmp_path / "secret.env")


class _FakeClient:
    """Routes the few API calls Launcher.submit makes for a no-sync spec."""

    def __init__(self) -> None:
        self.created: list[dict] = []

    def post(self, path: str, json: dict | None = None) -> dict:
        assert path == "/api/jobs"
        self.created.append(json or {})
        return {"responses": [{"id": "424242"}]}

    def get(self, path: str, params: dict | None = None) -> dict:
        if path.endswith("byNameAndVersion"):
            return {
                "invocation": {
                    "parameters": [
                        {"name": "initScript", "type": "input_file", "optional": True},
                        {"name": "batchScript", "type": "input_file", "optional": True},
                    ]
                }
            }
        raise AssertionError(f"unexpected GET {path}")

    @property
    def username(self) -> str:
        return "test"


_JOB = {
    "application": {"name": "app", "version": "1"},
    "product": {"id": "p", "category": "c", "provider": "u"},
}


def test_submit_plain_spec_only_creates_job() -> None:
    client = _FakeClient()
    job_id = Launcher(client).submit(parse_launch_spec(dict(_JOB)), tag="x")  # type: ignore[arg-type]
    assert job_id == "424242"
    assert len(client.created) == 1


def test_resolve_setup_param_prefers_batch_for_run() -> None:
    client = _FakeClient()
    launcher = Launcher(client)  # type: ignore[arg-type]
    spec = parse_launch_spec({**_JOB, "sync": {"remote": "/1/r"}, "setup": {"run": "echo hi"}})
    assert launcher._resolve_setup_param(spec) == "batchScript"
    spec2 = parse_launch_spec({**_JOB, "sync": {"remote": "/1/r"}, "setup": {"python": "uv"}})
    assert launcher._resolve_setup_param(spec2) == "initScript"


def test_exit_file_only_for_batch_runs() -> None:
    client = _FakeClient()
    launcher = Launcher(client)  # type: ignore[arg-type]
    batch = parse_launch_spec({**_JOB, "sync": {"remote": "/1/r"}, "setup": {"run": "x"}})
    assert launcher.exit_file_path(batch, "job1") == "/1/r/.ucloud/exit-job1"
    plain = parse_launch_spec(dict(_JOB))
    assert launcher.exit_file_path(plain, "job1") is None
