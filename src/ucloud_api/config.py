"""Configuration and credential storage.

Resolution order (highest priority first):

1. Explicit arguments passed in code.
2. Environment variables ``UCLOUD_REFRESH_TOKEN`` / ``UCLOUD_BASE_URL``.
3. The on-disk credentials file (``~/.config/ucloud-api/credentials.json`` on Linux).

The credentials file is written with ``0600`` permissions because it holds a
long-lived refresh token that is equivalent to a password.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_dir

from .exceptions import ConfigError

APP_NAME = "ucloud-api"
DEFAULT_BASE_URL = "https://cloud.sdu.dk"

ENV_REFRESH_TOKEN = "UCLOUD_REFRESH_TOKEN"
ENV_BASE_URL = "UCLOUD_BASE_URL"


def config_dir() -> Path:
    """Return the per-user config directory, honouring ``UCLOUD_CONFIG_DIR``."""
    override = os.environ.get("UCLOUD_CONFIG_DIR")
    return Path(override) if override else Path(user_config_dir(APP_NAME))


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def token_cache_path() -> Path:
    return config_dir() / "token_cache.json"


@dataclass(slots=True)
class Credentials:
    """Everything needed to talk to a UCloud deployment."""

    refresh_token: str
    base_url: str = DEFAULT_BASE_URL

    def save(self, path: Path | None = None) -> Path:
        """Persist credentials to disk with restrictive permissions."""
        target = path or credentials_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"refresh_token": self.refresh_token, "base_url": self.base_url}
        # Write then chmod, so the token is never briefly world-readable.
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        target.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        return target


def load_credentials(
    *,
    refresh_token: str | None = None,
    base_url: str | None = None,
) -> Credentials:
    """Resolve credentials from arguments, environment, then the config file."""
    base_url = base_url or os.environ.get(ENV_BASE_URL)
    refresh_token = refresh_token or os.environ.get(ENV_REFRESH_TOKEN)

    if not refresh_token or not base_url:
        stored = _load_credentials_file()
        if stored is not None:
            refresh_token = refresh_token or stored.get("refresh_token")
            base_url = base_url or stored.get("base_url")

    if not refresh_token:
        raise ConfigError(
            "No UCloud refresh token found. Run `ucloud login` or set "
            f"{ENV_REFRESH_TOKEN}. See the README for how to obtain a token."
        )

    return Credentials(refresh_token=refresh_token, base_url=base_url or DEFAULT_BASE_URL)


def _load_credentials_file() -> dict[str, str] | None:
    path = credentials_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read credentials file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Malformed credentials file {path}")
    return data
