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
from pathlib import Path

from platformdirs import user_config_path, user_data_path

POWERCONTEXT_HOME_ENV = "POWERCONTEXT_HOME"
DEFAULT_SERVER_ENV_FILE = Path(".env")


def default_server_env_file() -> Path:
    """Return the persistent configuration path for a personal Server."""

    return user_config_path("powercontext", appauthor=False) / "server.env"


def resolve_server_environment_file(
    env_file: Path | None,
    *,
    discover: bool,
    directory: Path | None = None,
) -> Path | None:
    """Select an explicit file, user configuration, or a legacy working-directory file."""

    if env_file is not None:
        expanded = env_file.expanduser()
        return Path(os.path.abspath(expanded))
    if not discover:
        return None
    persistent = default_server_env_file()
    if persistent.is_file():
        return persistent
    candidate = (Path.cwd() if directory is None else directory) / DEFAULT_SERVER_ENV_FILE
    return Path(os.path.abspath(candidate)) if candidate.is_file() else None


def powercontext_data_dir() -> Path:
    """Return the user data directory without creating it."""

    configured = os.environ.get(POWERCONTEXT_HOME_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
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
    "DEFAULT_SERVER_ENV_FILE",
    "POWERCONTEXT_HOME_ENV",
    "default_database_path",
    "default_scheduler_path",
    "default_seekdb_path",
    "default_server_env_file",
    "powercontext_data_dir",
    "resolve_server_environment_file",
    "sqlite_url",
]
