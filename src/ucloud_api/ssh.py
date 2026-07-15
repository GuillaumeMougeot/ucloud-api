"""Run commands on a running job over SSH.

We shell out to the system ``ssh`` binary rather than reimplementing SSH: it
already handles keys, agents and known-hosts the way the user expects.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from .exceptions import UCloudError
from .jobs import SSHEndpoint


@dataclass(slots=True)
class SSHRunner:
    """Execute commands against a UCloud job's SSH endpoint."""

    endpoint: SSHEndpoint
    identity_file: str | None = None
    # accept-new trusts a host's key on first sight but still detects changes,
    # which suits ephemeral jobs whose host key differs every launch.
    strict_host_key_checking: str = "accept-new"

    def _base_args(self) -> list[str]:
        args = [
            "ssh",
            "-p",
            str(self.endpoint.port),
            "-o",
            f"StrictHostKeyChecking={self.strict_host_key_checking}",
        ]
        if self.identity_file:
            args += ["-i", self.identity_file]
        args.append(f"{self.endpoint.user}@{self.endpoint.host}")
        return args

    def run(
        self,
        command: str,
        *,
        capture_output: bool = False,
        check: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a single command over SSH and return the completed process."""
        args = [*self._base_args(), command]
        try:
            return subprocess.run(
                args,
                capture_output=capture_output,
                text=True,
                check=check,
                timeout=timeout,
            )
        except FileNotFoundError as exc:  # no ssh client installed
            raise UCloudError("The `ssh` command was not found on this machine.") from exc
        except subprocess.CalledProcessError as exc:
            raise UCloudError(f"Remote command failed (exit {exc.returncode}): {command}") from exc

    def interactive_shell(self) -> int:
        """Open an interactive SSH session; return ssh's exit code."""
        completed = subprocess.run([*self._base_args()], check=False)
        return completed.returncode
