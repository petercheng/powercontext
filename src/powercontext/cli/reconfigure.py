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

"""Change one host's connection settings without reinstalling its plugin."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any

import typer

from powercontext.cli.native_transport import _home, _object_at, _read, resolve_host_transport
from powercontext.cli.transport import SetupTransport, hermes_config_file, save_setup_transport
from powercontext.client.transport_policy import client_config_file


def configure_connection(settings: SetupTransport, *, json_output: bool = False) -> None:
    """Apply one explicit endpoint change, retaining unrelated host preferences."""

    from powercontext.cli.authorization import (
        configure_stored_authorization,
        credential_path,
        setup_authorization_value,
    )
    from powercontext.cli.system import SetupError, _write_bytes_atomically

    try:
        updates = _native_updates(settings)
        destinations = [path for path, _payload in updates]
        destinations.append(client_config_file())
        if settings.host == "hermes":
            destinations.append(hermes_config_file())
        has_credentials = settings.host not in {"hermes", "openclaw"}
        if has_credentials:
            destinations.append(credential_path(settings.host))
        snapshots = {path: path.read_bytes() if path.exists() else None for path in destinations}
    except (OSError, ValueError) as error:
        raise SetupError(f"Cannot prepare {settings.host} connection settings: {error}") from error  # noqa: TRY003

    try:
        for path, payload in updates:
            _write_bytes_atomically(path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode())
        save_setup_transport(settings)
        authorization_state = (
            configure_stored_authorization(
                settings.host, server_url=settings.server_url, value=setup_authorization_value(settings.host)
            )
            if has_credentials
            else "host_managed"
        )
    except (OSError, ValueError, SetupError) as error:
        _restore_configuration(snapshots)
        raise SetupError(f"Could not update {settings.host} connection settings: {error}") from error  # noqa: TRY003

    _report_connection(settings, destinations, authorization_state, json_output=json_output)


def _restore_configuration(snapshots: dict[Path, bytes | None]) -> None:
    from powercontext.cli.system import _write_bytes_atomically

    for path, original in reversed(list(snapshots.items())):
        try:
            if original is None:
                path.unlink(missing_ok=True)
            else:
                _write_bytes_atomically(path, original)
        except OSError:
            typer.echo(f"WARNING: could not restore {path}; inspect its connection settings.", err=True)


def _report_connection(
    settings: SetupTransport, destinations: list[Path], authorization_state: str, *, json_output: bool
) -> None:
    warnings: list[str] = []
    if authorization_state == "url_mismatch":
        warnings.append("The saved credential belongs to another URL; provide the credential for this endpoint.")
    try:
        effective_url, _allowed = resolve_host_transport(settings.host)
        if effective_url != settings.server_url:
            warnings.append(f"An existing override still selects {effective_url}; update it and reload the host.")
    except ValueError as error:
        effective_url = None
        warnings.append(str(error))
    report = {
        "host": settings.host,
        "server_url": settings.server_url,
        "effective_server_url": effective_url,
        "client_configuration_file": str(client_config_file()),
        "configuration_files": [str(path) for path in destinations if path.exists()],
        "authorization_state": authorization_state,
        "reload_required": True,
        "warnings": warnings,
    }
    if json_output:
        typer.echo(json.dumps(report, indent=2, ensure_ascii=False))
        return
    typer.echo(f"Configured {settings.host}: {settings.server_url}")
    typer.echo(f"Client configuration: {client_config_file()}")
    for warning in warnings:
        typer.echo(f"WARNING: {warning}")
    typer.echo(f"Reload {settings.host} or start a new session, then run `powercontext doctor {settings.host}`.")


def _native_updates(settings: SetupTransport) -> list[tuple[Path, dict[str, Any]]]:
    if settings.host == "codex":
        return [_codex_update(settings)]
    if settings.host == "claude-code":
        return [_claude_update(settings)]
    if settings.host == "workbuddy":
        path = _home("WORKBUDDY_HOME", ".workbuddy") / "mcp.json"
        document = _read(path)
        entry = _object_at(document, "mcpServers", "powercontext")
        if entry.get("type") != "http":
            raise ValueError("Install the PowerContext HTTP MCP integration before reconfiguring it")  # noqa: TRY003
        entry["url"] = f"${{POWERCONTEXT_WORKBUDDY_SERVER_URL:-{settings.server_url}}}/mcp"
        return [(path, document)]
    if settings.host == "openclaw":
        configured = os.environ.get("OPENCLAW_CONFIG_PATH")
        path = (
            Path(configured).expanduser() if configured else _home("OPENCLAW_STATE_DIR", ".openclaw") / "openclaw.json"
        )
        document = _read(path)
        plugin = _object_at(document, "plugins", "entries", "memory-powercontext")
        if not plugin:
            raise ValueError("Install the PowerContext OpenClaw plugin before reconfiguring it")  # noqa: TRY003
        config = plugin.setdefault("config", {})
        if not isinstance(config, dict):
            raise ValueError("OpenClaw PowerContext configuration must be an object")  # noqa: TRY003
        config.update(endpoint=settings.server_url, allowInsecureHttp=settings.allow_insecure_http)
        return [(path, document)]
    # These clients already consume clients.json. Hermes' native file is paired
    # with that write by save_setup_transport.
    return []


def _codex_update(settings: SetupTransport) -> tuple[Path, dict[str, Any]]:
    from powercontext.cli.native_transport import installed_codex_configuration_file

    path = installed_codex_configuration_file()
    document = _read(path)
    entry = _object_at(document, "mcpServers", "powercontext")
    if entry.get("type") != "http":
        raise ValueError("Expected an installed HTTP MCP server")  # noqa: TRY003
    entry["url"] = settings.server_url + "/mcp"
    return path, document


def _claude_update(settings: SetupTransport) -> tuple[Path, dict[str, Any]]:
    from powercontext.cli.system import _is_powercontext_statusline

    path = _home("CLAUDE_CONFIG_DIR", ".claude") / "settings.json"
    document = _read(path)
    plugin = _object_at(document, "pluginConfigs", "powercontext@powercontext")
    if not plugin:
        raise ValueError("Install the PowerContext Claude Code plugin before reconfiguring it")  # noqa: TRY003
    options = plugin.setdefault("options", {})
    if not isinstance(options, dict):
        raise ValueError("Claude Code PowerContext options must be an object")  # noqa: TRY003, TRY004
    options.update(server_url=settings.server_url, allow_insecure_http=settings.allow_insecure_http)
    statusline = document.get("statusLine")
    if isinstance(statusline, dict) and _is_powercontext_statusline(statusline):
        arguments = shlex.split(statusline["command"])
        if "--server-url" in arguments:
            index = arguments.index("--server-url") + 1
            if index >= len(arguments):
                raise ValueError("PowerContext statusLine is missing its --server-url value")  # noqa: TRY003
            arguments[index] = settings.server_url
            statusline["command"] = shlex.join(arguments)
    return path, document
