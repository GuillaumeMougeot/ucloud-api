"""An ncdu-like browser of UCloud storage, backed by the provider's storage index.

Each folder is one ``browse`` call with ``includeSizes``: the provider answers with every
subfolder's total size from its index, so drilling down is instant however large the tree.
:class:`Browser` holds the navigation state and is independent of the terminal; :func:`run`
draws it with ``curses``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .exceptions import APIError
from .files import FileEntry

Loader = Callable[[str], list[FileEntry]]

HELP = "↑↓ move  →/enter open  ←/backspace up  s sort  r reload  q quit"


def entry_size(entry: FileEntry) -> int | None:
    """Bytes an entry takes: a folder's indexed total, or a file's own size."""
    return entry.total_size if entry.is_dir else entry.size


@dataclass
class _Dir:
    entries: list[FileEntry]
    cursor: int = 0
    top: int = 0  # first visible row


@dataclass
class Browser:
    """Navigation state: the current folder, its sorted entries and the cursor."""

    load: Loader
    path: str
    by_name: bool = False
    message: str = ""
    _cache: dict[str, _Dir] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.path = self.path.rstrip("/") or "/"
        self._open(self.path)

    # -- state ---------------------------------------------------------------
    @property
    def current(self) -> _Dir:
        return self._cache[self.path]

    @property
    def entries(self) -> list[FileEntry]:
        return self.current.entries

    @property
    def selected(self) -> FileEntry | None:
        d = self.current
        return d.entries[d.cursor] if d.entries else None

    @property
    def total(self) -> int:
        return sum(entry_size(e) or 0 for e in self.entries)

    # -- actions -------------------------------------------------------------
    def move(self, delta: int) -> None:
        d = self.current
        if d.entries:
            d.cursor = max(0, min(len(d.entries) - 1, d.cursor + delta))

    def enter(self) -> None:
        entry = self.selected
        if entry is None or not entry.is_dir:
            return
        target = entry.path.rstrip("/")
        if self._open(target):
            self.path = target

    def up(self) -> None:
        """Go to the parent folder, with the cursor on the folder we came from."""
        parent = self.path.rsplit("/", 1)[0]
        if not parent:  # already at a drive root (/<driveId>)
            return
        came_from = self.path
        if not self._open(parent):
            return
        self.path = parent
        d = self.current
        for i, e in enumerate(d.entries):
            if e.path.rstrip("/") == came_from:
                d.cursor = i
                break

    def toggle_sort(self) -> None:
        self.by_name = not self.by_name
        for d in self._cache.values():
            keep = d.entries[d.cursor] if d.entries else None
            self._sort(d.entries)
            d.cursor = d.entries.index(keep) if keep is not None else 0

    def reload(self) -> None:
        self._cache.clear()
        if not self._open(self.path):
            # The folder is gone or unreadable now: fall back to the drive root.
            self.path = "/" + self.path.strip("/").split("/", 1)[0]
            self._open(self.path)

    # -- internals -----------------------------------------------------------
    def _open(self, path: str) -> bool:
        if path in self._cache:
            self.message = ""
            return True
        try:
            entries = self.load(path)
        except APIError as exc:
            self.message = f"cannot open {path}: {exc}"
            if not self._cache:  # nothing to fall back on
                raise
            return False
        self._sort(entries)
        self._cache[path] = _Dir(entries)
        self.message = ""
        return True

    def _sort(self, entries: list[FileEntry]) -> None:
        if self.by_name:
            entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        else:
            entries.sort(key=lambda e: (-(entry_size(e) or 0), e.name.lower()))


Formatter = Callable[[int | None], str]


def format_row(entry: FileEntry, parent_total: int, width: int, fmt: Formatter) -> str:
    """One listing line: size, share of the folder, a bar, and the name (folders end in /)."""
    size = entry_size(entry)
    share = (size or 0) / parent_total if parent_total else 0.0
    bar_w = 10
    filled = round(share * bar_w)
    bar = "#" * filled + " " * (bar_w - filled)
    size_txt = fmt(size) if size is not None else "?"
    name = entry.name + ("/" if entry.is_dir else "")
    line = f"{size_txt:>10} {share * 100:5.1f}% [{bar}] {name}"
    return line[:width]


def run(browser: Browser, fmt: Formatter, footer: str = "") -> None:
    """Draw ``browser`` full-screen until the user quits."""
    import curses

    curses.wrapper(_loop, browser, fmt, footer)


def _loop(stdscr: Any, browser: Browser, fmt: Formatter, footer: str) -> None:
    import curses

    curses.curs_set(0)
    keys_up = (curses.KEY_UP, ord("k"))
    keys_down = (curses.KEY_DOWN, ord("j"))
    keys_enter = (curses.KEY_RIGHT, curses.KEY_ENTER, 10, 13, ord("l"))
    keys_back = (curses.KEY_LEFT, curses.KEY_BACKSPACE, 127, 8, ord("h"), ord("u"))
    while True:
        height, width = stdscr.getmaxyx()
        rows = max(1, height - 4)
        d = browser.current
        if d.cursor < d.top:
            d.top = d.cursor
        elif d.cursor >= d.top + rows:
            d.top = d.cursor - rows + 1

        stdscr.erase()
        total = browser.total
        head = f" {browser.path}  —  {fmt(total)} in {len(browser.entries)} item(s)"
        _put(stdscr, 0, head, width, curses.A_REVERSE)
        for i, entry in enumerate(browser.entries[d.top : d.top + rows]):
            idx = d.top + i
            attr = curses.A_REVERSE if idx == d.cursor else (curses.A_BOLD if entry.is_dir else 0)
            _put(stdscr, 1 + i, format_row(entry, total, width - 1, fmt), width, attr)
        if not browser.entries:
            _put(stdscr, 1, "   (empty folder)", width, curses.A_DIM)
        _put(stdscr, height - 2, browser.message or footer, width, curses.A_DIM)
        sort = "name" if browser.by_name else "size"
        _put(stdscr, height - 1, f" {HELP}  [sort: {sort}]", width, curses.A_REVERSE)
        stdscr.refresh()

        key = stdscr.getch()
        if key in (ord("q"), 27):
            return
        if key in keys_up:
            browser.move(-1)
        elif key in keys_down:
            browser.move(1)
        elif key == curses.KEY_PPAGE:
            browser.move(-rows)
        elif key == curses.KEY_NPAGE:
            browser.move(rows)
        elif key in (curses.KEY_HOME, ord("g")):
            browser.move(-len(browser.entries))
        elif key in (curses.KEY_END, ord("G")):
            browser.move(len(browser.entries))
        elif key in keys_enter:
            _busy(stdscr, height, width)
            browser.enter()
        elif key in keys_back:
            _busy(stdscr, height, width)
            browser.up()
        elif key in (ord("s"), ord("n")):
            browser.toggle_sort()
        elif key == ord("r"):
            _busy(stdscr, height, width)
            browser.reload()


def _busy(stdscr: Any, height: int, width: int) -> None:
    _put(stdscr, height - 2, " loading …", width, 0)
    stdscr.refresh()


def _put(stdscr: Any, y: int, text: str, width: int, attr: int) -> None:
    import curses

    with contextlib.suppress(curses.error):  # writing to the last cell of a tiny window
        stdscr.addnstr(y, 0, text.ljust(width), width - 1, attr)
