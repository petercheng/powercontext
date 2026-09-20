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
from powercontext.cli.transport import (
    SetupTransport,
    is_remote_http,
    prepare_setup_transport,
    save_setup_transport,
)
from powercontext.client.transport_policy import client_config_file


def configure_connection(
    host: str,
    *,
    server_url: str | None = None,
    allow_insecure_http: bool | None = None,
    json_output: bool = False,
) -> None:
    """Update connection files; leave authentication and health checks to doctor."""

    from powercontext.cli.system import SetupError

    try:
        settings = prepare_setup_transport(
            host, server_url=server_url, allow_insecure_http=allow_insecure_http, json_output=json_output
        )
        save_setup_transport(settings, native_updates=_native_updates(settings))
    except (OSError, ValueError, SetupError) as error:
        if json_output:
            typer.echo(json.dumps({"host": host, "status": "failed", "error": str(error)}))
        else:
            typer.echo(f"Connection configuration failed: {error}", err=True)
        raise typer.Exit(code=1) from error

    effective, warnings = _connection_warnings(settings)
    status = "needs_attention" if warnings else "applied"
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "host": host,
                    "status": status,
                    "server_url": settings.server_url,
                    "effective_server_url": effective,
                    "client_configuration_file": str(client_config_file()),
                    "warnings": warnings,
                },
                indent=2,
            )
        )
    else:
        typer.echo(f"Connection configuration {status.replace('_', ' ')}: {host}")
        typer.echo(f"Client configuration: {client_config_file()}")
        typer.echo(f"Saved URL: {settings.server_url}; effective URL: {effective or 'unknown'}")
        for warning in warnings:
            typer.echo(f"WARNING: {warning}", err=True)
        typer.echo(f"Reload {host}, then run `powercontext doctor {host}` to check authentication and connectivity.")
    if warnings:
        raise typer.Exit(code=3)


def _connection_warnings(settings: SetupTransport) -> tuple[str | None, list[str]]:
    try:
        effective, allowed = resolve_host_transport(settings.host)
    except ValueError as error:
        return None, [str(error)]
    warnings = []
    if effective != settings.server_url:
        warnings.append(f"An existing override still selects {effective}; update it before reloading the host.")
    if is_remote_http(effective) and not allowed:
        warnings.append("The effective configuration blocks remote HTTP; update the consent override or use HTTPS.")
    return effective, warnings


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
