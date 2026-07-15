"""An interactive file browser: ``ucloud files shell``.

Gives a small REPL with ``cd`` / ``ls`` / ``pwd`` / ``get`` / ``put`` / ``mkdir``
/ ``rm`` and **tab-completion** of remote paths. The root (``/``) lists your
drives; ``cd`` into one to navigate its contents.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from rich.console import Console
from rich.table import Table

from .client import UCloudClient
from .exceptions import UCloudError
from .files import Files
from .transfer import Transfer

console = Console()
err_console = Console(stderr=True)


def resolve_path(cwd: str, arg: str) -> str:
    """Resolve ``arg`` (absolute or relative, with ``.``/``..``) against ``cwd``."""
    base = arg if arg.startswith("/") else f"{cwd.rstrip('/')}/{arg}"
    parts: list[str] = []
    for seg in base.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
        else:
            parts.append(seg)
    return "/" + "/".join(parts)


class RemotePathCompleter(Completer):
    """Completes the path token under the cursor from live UCloud listings."""

    def __init__(self, files: Files, cwd_getter: Callable[[], str]) -> None:
        self._files = files
        self._cwd = cwd_getter
        self._cache: dict[str, list[tuple[str, bool]]] = {}

    def _entries(self, dirpath: str) -> list[tuple[str, bool]]:
        """Return (name, is_dir) for a directory, cached; drives at the root."""
        if dirpath not in self._cache:
            try:
                if dirpath == "/":
                    self._cache[dirpath] = [(d.id, True) for d in self._files.list_drives()]
                else:
                    self._cache[dirpath] = [
                        (e.name, e.is_dir) for e in self._files.list_path(dirpath)
                    ]
            except UCloudError:
                self._cache[dirpath] = []
        return self._cache[dirpath]

    def get_completions(self, document: Document, complete_event: object) -> Iterable[Completion]:
        text = document.text_before_cursor
        # Only complete the path argument, not the command word itself.
        if " " not in text:
            return
        # `cd` only makes sense for directories; other commands accept files too.
        dirs_only = text.split(None, 1)[0] == "cd"
        partial = "" if text.endswith(" ") else text.rsplit(None, 1)[-1]
        head, _, prefix = partial.rpartition("/")
        if partial.startswith("/"):
            dirpath = resolve_path("/", head) if head else "/"
        else:
            dirpath = resolve_path(self._cwd(), head) if head else self._cwd()
        prefix_lower = prefix.lower()  # case-insensitive so `tr` finds `Trash/`
        for name, is_dir in self._entries(dirpath):
            if dirs_only and not is_dir:
                continue
            if name.lower().startswith(prefix_lower):
                completion = name + ("/" if is_dir else "")
                yield Completion(completion, start_position=-len(prefix), display=completion)


class FilesShell:
    def __init__(self, client: UCloudClient, start: str = "/") -> None:
        self._files = Files(client)
        self._transfer = Transfer(client)
        self.cwd = start if start.startswith("/") else "/"

    def run(self) -> None:
        completer = RemotePathCompleter(self._files, lambda: self.cwd)
        session: PromptSession[str] = PromptSession(completer=completer)
        console.print(
            "[dim]ucloud files shell — try ls, cd, pwd, get, put, mkdir, rm, help, exit. "
            "Tab completes paths.[/]"
        )
        while True:
            try:
                line = session.prompt(f"ucloud:{self.cwd}$ ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                return
            if not line:
                continue
            parts = line.split()
            cmd, args = parts[0], parts[1:]
            if cmd in ("exit", "quit"):
                return
            handler = getattr(self, f"_cmd_{cmd}", None)
            if handler is None:
                err_console.print(f"[red]unknown command:[/] {cmd} (try 'help')")
                continue
            try:
                handler(args)
            except UCloudError as exc:
                err_console.print(f"[red]error:[/] {_friendly(exc)}")
            # Listings may have changed; drop the completer cache.
            completer._cache.clear()

    # -- commands ----------------------------------------------------------- #

    def _cmd_help(self, _args: list[str]) -> None:
        console.print(
            "Commands:\n"
            "  ls [path]           list a directory (root lists drives)\n"
            "  cd <path>           change directory (.. and absolute paths work)\n"
            "  pwd                 print the current directory\n"
            "  get <remote> [local]  download a file or folder\n"
            "  put <local> [remote]  upload a file or folder\n"
            "  mkdir <path>        create a folder\n"
            "  rm <path>           move to trash\n"
            "  exit                leave the shell"
        )

    def _cmd_pwd(self, _args: list[str]) -> None:
        console.print(self.cwd)

    def _cmd_ls(self, args: list[str]) -> None:
        target = resolve_path(self.cwd, args[0]) if args else self.cwd
        if target == "/":
            table = Table("Path", "Title")
            for d in self._files.list_drives():
                table.add_row(d.path, d.title)
            console.print(table)
            return
        entries = self._files.list_path(target)
        if not entries:
            console.print("[dim](empty)[/]")
            return
        for e in entries:
            console.print(f"[blue]{e.name}/[/]" if e.is_dir else e.name)

    def _cmd_cd(self, args: list[str]) -> None:
        if not args:
            self.cwd = "/"
            return
        target = resolve_path(self.cwd, args[0])
        if target == "/":
            self.cwd = "/"
            return
        info = self._files.stat(target)
        if info is None or not info.is_dir:
            raise UCloudError(f"Not a directory: {target}")
        self.cwd = target

    def _cmd_mkdir(self, args: list[str]) -> None:
        if not args:
            raise UCloudError("usage: mkdir <path>")
        path = resolve_path(self.cwd, args[0])
        self._files.mkdir(path)
        console.print(f"[green]created[/] {path}")

    def _cmd_rm(self, args: list[str]) -> None:
        if not args:
            raise UCloudError("usage: rm <path>")
        path = resolve_path(self.cwd, args[0])
        self._files.trash(path)
        console.print(f"[green]trashed[/] {path}")

    def _cmd_get(self, args: list[str]) -> None:
        if not args:
            raise UCloudError("usage: get <remote> [local]")
        remote = resolve_path(self.cwd, args[0])
        local = Path(args[1]) if len(args) > 1 else Path(remote.rsplit("/", 1)[-1])
        stats = self._transfer.download(remote, local)
        console.print(f"[green]downloaded[/] {stats.files} file(s) -> {local}")

    def _cmd_put(self, args: list[str]) -> None:
        if not args:
            raise UCloudError("usage: put <local> [remote]")
        local = Path(args[0])
        remote = resolve_path(self.cwd, args[1]) if len(args) > 1 else f"{self.cwd.rstrip('/')}/"
        stats = self._transfer.upload(local, remote)
        console.print(f"[green]uploaded[/] {stats.files} file(s) -> {remote}")


def _friendly(exc: UCloudError) -> str:
    from .exceptions import APIError

    if isinstance(exc, APIError) and exc.status_code in (403, 404):
        return (
            "path not found or not accessible — check the path, and that you're in the "
            "right project (`ucloud projects`)."
        )
    return str(exc)
