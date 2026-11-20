"""The ncdu-like browser's navigation, on a synthetic indexed tree."""

from __future__ import annotations

import pytest

from ucloud_api.exceptions import APIError
from ucloud_api.files import FileEntry
from ucloud_api.ncdu import Browser, format_row


def _dir(path: str, total: int | None) -> FileEntry:
    return FileEntry(path=path, type="DIRECTORY", size=0, modified_at=None, total_size=total)


def _file(path: str, size: int) -> FileEntry:
    return FileEntry(path=path, type="FILE", size=size, modified_at=None)


_TREE = {
    "/7": [_dir("/7/small", 10), _dir("/7/big", 900), _file("/7/mid.bin", 90)],
    "/7/big": [_dir("/7/big/inner", 800), _file("/7/big/x.bin", 100)],
    "/7/big/inner": [],
    "/7/small": [_dir("/7/small/fresh", None), _file("/7/small/a.txt", 10)],
}


class _Loader:
    def __init__(self, broken=()) -> None:
        self.calls: list[str] = []
        self.broken = set(broken)

    def __call__(self, path: str) -> list[FileEntry]:
        self.calls.append(path)
        if path in self.broken:
            raise APIError("forbidden", status_code=403)
        return [FileEntry(e.path, e.type, e.size, e.modified_at, e.total_size) for e in _TREE[path]]


def test_entries_are_sorted_largest_first_files_included() -> None:
    b = Browser(load=_Loader(), path="/7/")
    assert b.path == "/7"
    assert [e.name for e in b.entries] == ["big", "mid.bin", "small"]
    assert b.total == 1000


def test_enter_and_up_return_to_the_folder_you_came_from() -> None:
    loader = _Loader()
    b = Browser(load=loader, path="/7")
    b.move(2)  # small
    b.enter()
    assert b.path == "/7/small"
    b.up()
    assert b.path == "/7" and b.selected is not None and b.selected.name == "small"
    b.move(-5)
    b.enter()
    b.enter()
    assert b.path == "/7/big/inner" and b.entries == []
    b.up()
    b.up()
    assert b.path == "/7" and loader.calls.count("/7") == 1  # folders are listed once


def test_files_are_not_entered_and_drive_root_is_the_top() -> None:
    b = Browser(load=_Loader(), path="/7")
    b.move(1)  # mid.bin
    b.enter()
    assert b.path == "/7"
    b.up()
    assert b.path == "/7"


def test_unreadable_folder_keeps_the_view_and_says_why() -> None:
    b = Browser(load=_Loader(broken={"/7/big"}), path="/7")
    b.enter()
    assert b.path == "/7" and "cannot open /7/big" in b.message
    with pytest.raises(APIError):
        Browser(load=_Loader(broken={"/7"}), path="/7")


def test_toggle_sort_keeps_the_cursor_on_the_same_entry() -> None:
    b = Browser(load=_Loader(), path="/7")
    b.move(1)  # mid.bin
    b.toggle_sort()
    assert [e.name for e in b.entries] == ["big", "small", "mid.bin"]
    assert b.selected is not None and b.selected.name == "mid.bin"


def test_reload_lists_again() -> None:
    loader = _Loader()
    b = Browser(load=loader, path="/7")
    b.reload()
    assert loader.calls == ["/7", "/7"]


def test_format_row_shows_share_bar_and_unknown_sizes() -> None:
    fmt = lambda n: f"{n} B"  # noqa: E731
    row = format_row(_dir("/7/big", 900), 1000, 80, fmt)
    assert row == "     900 B  90.0% [######### ] big/"
    unindexed = format_row(_dir("/7/small/fresh", None), 10, 80, fmt)
    assert unindexed.strip().startswith("?") and unindexed.endswith("fresh/")
    assert len(format_row(_file("/7/" + "n" * 200, 1), 1, 40, fmt)) == 40
