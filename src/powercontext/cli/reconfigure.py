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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import typer

from powercontext.cli.native_transport import _home, _object_at, _read, resolve_host_transport
from powercontext.cli.transport import (
    SetupTransport,
    hermes_config_file,
    is_remote_http,
    prepare_setup_transport,
    save_setup_transport,
)
from powercontext.client.transport_policy import client_config_file


@dataclass
class ConnectionResult:
    """Describe configuration changes independently of live connection health."""

    host: str
    client_configuration_file: str
    status: Literal["applied", "needs_attention", "failed"] = "failed"
    server_url: str | None = None
    effective_server_url: str | None = None
    configuration_files: list[str] = field(default_factory=list)
    authorization_state: str = "not_checked"
    reload_required: bool = False
    connection_status: Literal["not_checked"] = "not_checked"
    warnings: list[str] = field(default_factory=list)
    error: dict[str, str] | None = None
    rollback_status: Literal["not_needed", "restored", "incomplete"] = "not_needed"
    unrestored_files: list[str] = field(default_factory=list)


def configure_connection(
    host: str,
    *,
    server_url: str | None = None,
    allow_insecure_http: bool | None = None,
    json_output: bool = False,
) -> None:
    """Write connection settings and report applied, needs_attention, or failed."""

    result = _configure_connection(
        host, server_url=server_url, allow_insecure_http=allow_insecure_http, json_output=json_output
    )
    _write_result(result, json_output=json_output)
    exit_code = {"applied": 0, "needs_attention": 3, "failed": 1}[result.status]
    if exit_code:
        raise typer.Exit(code=exit_code)


def _configure_connection(
    host: str, *, server_url: str | None, allow_insecure_http: bool | None, json_output: bool
) -> ConnectionResult:
    from powercontext.cli.authorization import (
        configure_stored_authorization,
        credential_path,
        setup_authorization_value,
    )
    from powercontext.cli.system import SetupError, _write_bytes_atomically

    result = ConnectionResult(host=host, client_configuration_file=str(client_config_file()))
    try:
        settings = prepare_setup_transport(
            host, server_url=server_url, allow_insecure_http=allow_insecure_http, json_output=json_output
        )
        result.server_url = settings.server_url
        updates = _native_updates(settings)
        destinations = [path for path, _payload in updates]
        destinations.append(client_config_file())
        if host == "hermes":
            destinations.append(hermes_config_file())
        has_credentials = host not in {"hermes", "openclaw"}
        if has_credentials:
            destinations.append(credential_path(host))
        result.configuration_files = [str(path) for path in destinations]
        snapshots = {path: path.read_bytes() if path.exists() else None for path in destinations}
    except (OSError, ValueError, SetupError) as error:
        result.error = {"stage": "prepare", "message": str(error)}
        return result

    try:
        for path, payload in updates:
            _write_bytes_atomically(path, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode())
        save_setup_transport(settings)
        result.authorization_state = (
            configure_stored_authorization(host, server_url=settings.server_url, value=setup_authorization_value(host))
            if has_credentials
            else "host_managed"
        )
    except (OSError, ValueError, SetupError) as error:
        result.error = {"stage": "write", "message": str(error)}
        result.unrestored_files = _restore_configuration(snapshots)
        result.rollback_status = "incomplete" if result.unrestored_files else "restored"
        return result

    if host == "codex":
        _configure_codex_authorization(settings.server_url, result)
    _inspect_connection(settings, result)
    return result


def _restore_configuration(snapshots: dict[Path, bytes | None]) -> list[str]:
    from powercontext.cli.system import _write_bytes_atomically

    unrestored = []
    for path, original in reversed(list(snapshots.items())):
        try:
            if original is None:
                path.unlink(missing_ok=True)
            elif not path.exists() or path.read_bytes() != original:
                _write_bytes_atomically(path, original)
        except OSError:
            unrestored.append(str(path))
    return unrestored


def _inspect_connection(settings: SetupTransport, result: ConnectionResult) -> None:
    authorization_warnings = {
        "url_mismatch": "The saved credential belongs to another URL; provide the credential for this endpoint.",
        "invalid": "The saved credential is invalid; replace it with a valid credential for this endpoint.",
        "unsafe_permissions": "The saved credential has unsafe permissions; restrict access before reloading the host.",
    }
    if warning := authorization_warnings.get(result.authorization_state):
        result.warnings.append(warning)
    try:
        result.effective_server_url, allowed = resolve_host_transport(settings.host)
        if result.effective_server_url != settings.server_url:
            result.warnings.append(
                f"An existing override still selects {result.effective_server_url}; update it and reload the host."
            )
        if is_remote_http(result.effective_server_url) and not allowed:
            result.warnings.append(
                "The effective configuration blocks remote HTTP; update the host's HTTP consent override "
                "or select HTTPS before reloading."
            )
    except ValueError as error:
        result.warnings.append(str(error))
    result.status = "needs_attention" if result.warnings else "applied"
    result.reload_required = True


def _configure_codex_authorization(server_url: str, result: ConnectionResult) -> None:
    from powercontext.cli.authorization import (
        configure_codex_desktop_authorization,
        credential_path,
        read_stored_authorization,
    )
    from powercontext.cli.system import _resolve_codex_native_authorization

    try:
        stored = read_stored_authorization(credential_path("codex"), server_url=server_url)
        if stored.authorization is not None:
            configure_codex_desktop_authorization(stored.authorization)
        diagnostic, _authorization = _resolve_codex_native_authorization(server_url + "/mcp")
    except OSError:
        result.warnings.append("Cannot update or read Codex Desktop authorization; run `powercontext doctor codex`.")
        return
    if not diagnostic.ok:
        result.warnings.append(
            "Codex native MCP authorization needs attention; set POWERCONTEXT_CODEX_AUTHORIZATION "
            "to the endpoint's complete Bearer credential in the host environment, then reload Codex."
        )


def _write_result(result: ConnectionResult, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        return
    failed = result.status == "failed"
    typer.echo(f"Connection configuration {result.status.replace('_', ' ')}: {result.host}", err=failed)
    typer.echo(f"Client configuration: {result.client_configuration_file}", err=failed)
    if result.error is not None:
        typer.echo(f"{result.error['stage']}: {result.error['message']}", err=True)
    if result.rollback_status != "not_needed":
        typer.echo(f"Rollback: {result.rollback_status}", err=True)
    for path in result.unrestored_files:
        typer.echo(f"WARNING: could not restore {path}; inspect its connection settings before reloading.", err=True)
    for warning in result.warnings:
        typer.echo(f"WARNING: {warning}")
    if failed:
        return
    typer.echo(f"Saved URL: {result.server_url}")
    typer.echo(f"Effective URL: {result.effective_server_url or 'unknown'}")
    typer.echo("Connection health: not checked.")
    typer.echo(f"Reload {result.host} or start a new session, then run `powercontext doctor {result.host}`.")


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
