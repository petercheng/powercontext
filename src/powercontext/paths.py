# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""User-owned paths for installed PowerContext processes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

POWERCONTEXT_HOME_ENV = "POWERCONTEXT_HOME"


def powercontext_config_dir() -> Path:
    """Locate user configuration, without depending on optional Server packages."""

    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        configured = os.environ.get("XDG_CONFIG_HOME", "")
        root = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / ".config"
    return root / "powercontext"


def default_server_env_file() -> Path:
    """Return the persistent configuration path for a personal Server."""

    return powercontext_config_dir() / "server.env"


def client_config_path() -> Path:
    """Prefer the platform path, retaining an existing legacy client file."""

    configured = os.environ.get("POWERCONTEXT_CLIENT_CONFIG_FILE")
    if configured:
        return Path(configured).expanduser()
    preferred = powercontext_config_dir() / "clients.json"
    legacy = Path.home() / ".config" / "powercontext" / "clients.json"
    return legacy if not preferred.exists() and legacy.is_file() else preferred


def powercontext_data_dir() -> Path:
    """Return the user data directory without creating it."""

    configured = os.environ.get(POWERCONTEXT_HOME_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    if sys.platform not in {"darwin", "win32"}:
        configured = os.environ.get("XDG_DATA_HOME", "")
        root = Path(configured) if configured and Path(configured).is_absolute() else Path.home() / ".local" / "share"
        return root / "powercontext"
    from platformdirs import user_data_path

    return user_data_path("powercontext", appauthor=False)


def default_database_path() -> Path:
    """Return the installed Server's default SQLite database path."""

    return powercontext_data_dir() / "powercontext.db"


def default_seekdb_path() -> Path:
    """Return the installed Server's default embedded seekdb directory."""

    return powercontext_data_dir() / "seekdb"


def default_scheduler_path() -> Path:
    """Return the installed Server's default scheduler database path."""

    return powercontext_data_dir() / "scheduler.db"


def sqlite_url(path: Path) -> str:
    """Render an absolute path as an async SQLAlchemy SQLite URL."""

    return f"sqlite+aiosqlite:///{path.expanduser().resolve().as_posix()}"


__all__ = [
    "POWERCONTEXT_HOME_ENV",
    "client_config_path",
    "default_database_path",
    "default_scheduler_path",
    "default_seekdb_path",
    "default_server_env_file",
    "powercontext_config_dir",
    "powercontext_data_dir",
    "sqlite_url",
]
