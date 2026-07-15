"""Interactive-shell path resolution and completion."""

from __future__ import annotations

import pytest
from prompt_toolkit.document import Document

from ucloud_api.files import Drive, FileEntry
from ucloud_api.shell import RemotePathCompleter, resolve_path


@pytest.mark.parametrize(
    ("cwd", "arg", "expected"),
    [
        ("/12347837", "data", "/12347837/data"),
        ("/12347837/a", "..", "/12347837"),
        ("/12347837/a/b", "../..", "/12347837"),
        ("/12347837", "/other/path", "/other/path"),
        ("/12347837", "./x/./y", "/12347837/x/y"),
        ("/", "12347837", "/12347837"),
        ("/12347837/a", "../../..", "/"),  # can't go above root
        ("/12347837", "sub/", "/12347837/sub"),
    ],
)
def test_resolve_path(cwd: str, arg: str, expected: str) -> None:
    assert resolve_path(cwd, arg) == expected


class _FakeFiles:
    def list_drives(self) -> list[Drive]:
        return [Drive(id="12347837", title="Member Files", provider="ucloud")]

    def list_path(self, path: str) -> list[FileEntry]:
        return [
            FileEntry(path="/12347837/dataset", type="DIRECTORY", size=None, modified_at=None),
            FileEntry(path="/12347837/notes.md", type="FILE", size=10, modified_at=None),
        ]


def _completions(completer: RemotePathCompleter, text: str) -> list[str]:
    doc = Document(text, cursor_position=len(text))
    return [c.text for c in completer.get_completions(doc, None)]


def test_completer_lists_dir_entries() -> None:
    completer = RemotePathCompleter(_FakeFiles(), lambda: "/12347837")  # type: ignore[arg-type]
    comps = _completions(completer, "ls da")
    assert "dataset/" in comps
    assert "notes.md" not in comps  # filtered by the "da" prefix


def test_completer_root_lists_drives() -> None:
    completer = RemotePathCompleter(_FakeFiles(), lambda: "/")  # type: ignore[arg-type]
    comps = _completions(completer, "cd ")
    assert "12347837/" in comps


def test_completer_ignores_command_word() -> None:
    completer = RemotePathCompleter(_FakeFiles(), lambda: "/12347837")  # type: ignore[arg-type]
    # No space yet -> completing the command, not a path.
    assert _completions(completer, "ls") == []
