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

"""Explicit connection changes preserve installation state and unrelated preferences."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.system import setup_app
from powercontext.client.transport_policy import client_config_file, load_client_settings

HOSTS = ("codex", "claude-code", "dsh", "openclaw", "pi", "opencode", "hermes", "workbuddy")
OLD_URL = "http://127.0.0.1:8000"
NEW_URL = "http://127.0.0.1:18321"


def _write(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


@pytest.fixture(autouse=True)
def host_configuration(tmp_path, monkeypatch):
    for name in os.environ:
        if name.startswith("POWERCONTEXT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    for name in (
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "WORKBUDDY_HOME",
        "HERMES_HOME",
        "DSH_HOME",
        "OPENCLAW_STATE_DIR",
        "PI_CODING_AGENT_DIR",
        "OPENCODE_CONFIG_DIR",
    ):
        monkeypatch.setenv(name, str(tmp_path / name))
    _write(
        tmp_path / "CODEX_HOME/plugins/cache/test/powercontext/1.0/.mcp.json",
        {
            "mcpServers": {"powercontext": {"type": "http", "url": OLD_URL + "/mcp", "required": False}},
        },
    )
    run = subprocess.run

    def host_command(command, *args, **kwargs):
        if command[0] == "codex":
            assert command[1:] == ["plugin", "list", "--json"]
            payload = {
                "installed": [
                    {
                        "name": "powercontext",
                        "installed": True,
                        "enabled": True,
                        "marketplaceName": "test",
                        "version": "1.0",
                    }
                ]
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", host_command)
    _write(
        tmp_path / "CLAUDE_CONFIG_DIR/settings.json",
        {
            "pluginConfigs": {
                "powercontext@powercontext": {"options": {"server_url": OLD_URL, "capture_prompts": False}}
            },
        },
    )
    _write(
        tmp_path / "WORKBUDDY_HOME/mcp.json",
        {
            "mcpServers": {
                "powercontext": {"type": "http", "url": OLD_URL + "/mcp", "disabled": False},
                "other": {"url": "https://other.example/mcp"},
            },
        },
    )
    _write(tmp_path / "HERMES_HOME/powercontext/config.json", {"base_url": OLD_URL, "capture": False})
    _write(
        tmp_path / "OPENCLAW_STATE_DIR/openclaw.json",
        {
            "plugins": {"entries": {"memory-powercontext": {"config": {"endpoint": OLD_URL, "autoCapture": False}}}},
        },
    )


@pytest.mark.parametrize("host", HOSTS)
def test_configure_only_changes_connection_without_an_installation(host, tmp_path):
    result = CliRunner().invoke(
        create_cli([setup_app]),
        [
            "setup",
            host,
            "--configure-only",
            "--server-url",
            NEW_URL,
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "applied"
    assert report["connection_status"] == "not_checked"
    assert report["error"] is None
    assert report["rollback_status"] == "not_needed"
    assert report["server_url"] == NEW_URL
    assert report["effective_server_url"] == NEW_URL
    assert report["reload_required"] is True
    assert load_client_settings(host)["server_url"] == NEW_URL
    if host == "claude-code":
        native = json.loads((tmp_path / "CLAUDE_CONFIG_DIR/settings.json").read_text())
        assert native["pluginConfigs"]["powercontext@powercontext"]["options"]["capture_prompts"] is False
    elif host == "hermes":
        native = json.loads((tmp_path / "HERMES_HOME/powercontext/config.json").read_text())
        assert native["capture"] is False
    elif host == "openclaw":
        native = json.loads((tmp_path / "OPENCLAW_STATE_DIR/openclaw.json").read_text())
        assert native["plugins"]["entries"]["memory-powercontext"]["config"]["autoCapture"] is False


def test_reconfiguration_keeps_url_bound_credentials_until_explicitly_replaced():
    from powercontext.cli.authorization import credential_path, write_stored_authorization

    path = credential_path("codex")
    write_stored_authorization(path, server_url=OLD_URL, value="old-secret")
    original = json.loads(path.read_text())

    result = CliRunner().invoke(
        create_cli([setup_app]),
        [
            "setup",
            "codex",
            "--configure-only",
            "--server-url",
            NEW_URL,
            "--json",
        ],
    )

    assert result.exit_code == 3, result.output
    report = json.loads(result.stdout)
    assert report["status"] == "needs_attention"
    assert report["authorization_state"] == "url_mismatch"
    assert report["warnings"]
    assert json.loads(path.read_text()) == original
    assert "old-secret" not in result.output


def test_failed_connection_write_restores_native_configuration(tmp_path, monkeypatch):
    native = tmp_path / "WORKBUDDY_HOME/mcp.json"
    original = native.read_bytes()
    replace = os.replace

    def fail_shared(source, destination):
        if Path(destination) == client_config_file():
            raise OSError("simulated disk failure")  # noqa: TRY003
        replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_shared)
    result = CliRunner().invoke(
        create_cli([setup_app]),
        [
            "setup",
            "workbuddy",
            "--configure-only",
            "--server-url",
            NEW_URL,
            "--json",
        ],
    )

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["status"] == "failed"
    assert report["error"]["stage"] == "write"
    assert report["rollback_status"] == "restored"
    assert report["unrestored_files"] == []
    assert report["reload_required"] is False
    assert native.read_bytes() == original
    assert not client_config_file().exists()


def test_configure_only_reports_an_environment_override(monkeypatch):
    monkeypatch.setenv("POWERCONTEXT_PI_BASE_URL", OLD_URL)
    result = CliRunner().invoke(
        create_cli([setup_app]),
        [
            "setup",
            "pi",
            "--configure-only",
            "--server-url",
            NEW_URL,
            "--json",
        ],
    )

    assert result.exit_code == 3, result.output
    report = json.loads(result.stdout)
    assert report["status"] == "needs_attention"
    assert report["connection_status"] == "not_checked"
    assert report["effective_server_url"] == OLD_URL
    assert report["warnings"]
    assert load_client_settings("pi")["server_url"] == NEW_URL


@pytest.mark.parametrize("host", ["codex", "claude-code", "hermes", "openclaw", "workbuddy"])
def test_repeated_setup_retains_native_endpoint_without_a_saved_client_file(host):
    result = CliRunner().invoke(create_cli([setup_app]), ["setup", host, "--configure-only", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["effective_server_url"] == OLD_URL
    assert load_client_settings(host)["server_url"] == OLD_URL


def test_codex_reconfiguration_selects_the_active_version(tmp_path):
    inactive = _write(
        tmp_path / "CODEX_HOME/plugins/cache/test/powercontext/0.9/.mcp.json",
        {
            "mcpServers": {"powercontext": {"type": "http", "url": OLD_URL + "/mcp"}},
        },
    )
    original = inactive.read_bytes()

    result = CliRunner().invoke(
        create_cli([setup_app]),
        [
            "setup",
            "codex",
            "--configure-only",
            "--server-url",
            NEW_URL,
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["effective_server_url"] == NEW_URL
    assert inactive.read_bytes() == original


@pytest.mark.parametrize("host", HOSTS)
def test_preparation_errors_are_json_for_every_host(host):
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", host, "--configure-only", "--server-url", "invalid", "--json"]
    )

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["host"] == host
    assert report["status"] == "failed"
    assert report["error"]["stage"] == "prepare"
    assert report["error"]["message"]
    assert report["rollback_status"] == "not_needed"
    assert report["connection_status"] == "not_checked"
    assert report["reload_required"] is False
    assert not client_config_file().exists()


def test_unavailable_native_configuration_is_reported_as_json(tmp_path):
    (tmp_path / "WORKBUDDY_HOME/mcp.json").unlink()

    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "workbuddy", "--configure-only", "--server-url", NEW_URL, "--json"]
    )

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["status"] == "failed"
    assert report["error"]["stage"] == "prepare"
    assert "Install" in report["error"]["message"]
    assert not client_config_file().exists()


def test_invalid_client_configuration_is_not_overwritten(tmp_path):
    client_config_file().write_text("invalid-json")
    native = tmp_path / "WORKBUDDY_HOME/mcp.json"
    original = native.read_bytes()

    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "workbuddy", "--configure-only", "--server-url", NEW_URL, "--json"]
    )

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["error"]["stage"] == "prepare"
    assert client_config_file().read_text() == "invalid-json"
    assert native.read_bytes() == original


def test_incomplete_rollback_reports_the_files_needing_repair(tmp_path, monkeypatch):
    native = tmp_path / "WORKBUDDY_HOME/mcp.json"
    shared = _write(client_config_file(), {"version": 1, "hosts": {}})
    originals = {path: path.read_bytes() for path in (native, shared)}
    replace = os.replace

    def disk_failure_after_progress(source, destination):
        if any(path.read_bytes() != original for path, original in originals.items()):
            raise OSError("simulated disk failure")  # noqa: TRY003
        replace(source, destination)

    monkeypatch.setattr(os, "replace", disk_failure_after_progress)
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "workbuddy", "--configure-only", "--server-url", NEW_URL, "--json"]
    )

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["status"] == "failed"
    assert report["rollback_status"] == "incomplete"
    changed = {str(path) for path, original in originals.items() if path.read_bytes() != original}
    assert changed
    assert set(report["unrestored_files"]) == changed


@pytest.mark.parametrize("state", ["invalid", "unsafe_permissions"])
def test_unusable_credentials_require_attention(state):
    from powercontext.cli.authorization import credential_path, write_stored_authorization

    if state == "unsafe_permissions" and os.name == "nt":
        pytest.skip("POSIX credential permissions are not checked on Windows")
    path = credential_path("pi")
    write_stored_authorization(path, server_url=NEW_URL, value="private-token")
    if state == "invalid":
        path.write_text('{"version": 1, "authorization": "private-token"}')
    else:
        path.chmod(0o644)
    original = path.read_bytes()

    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "pi", "--configure-only", "--server-url", NEW_URL, "--json"]
    )

    assert result.exit_code == 3, result.output
    report = json.loads(result.stdout)
    assert report["status"] == "needs_attention"
    assert report["authorization_state"] == state
    assert report["warnings"]
    assert path.read_bytes() == original
    assert "private-token" not in result.output


def test_unknown_effective_configuration_requires_attention(monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SERVER_URL", "invalid")

    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "claude-code", "--configure-only", "--server-url", NEW_URL, "--json"]
    )

    assert result.exit_code == 3, result.output
    report = json.loads(result.stdout)
    assert report["status"] == "needs_attention"
    assert report["server_url"] == NEW_URL
    assert report["effective_server_url"] is None
    assert report["warnings"]
    assert load_client_settings("claude-code")["server_url"] == NEW_URL


@pytest.mark.parametrize("url", [NEW_URL, "invalid"])
def test_text_output_distinguishes_attention_from_failure(monkeypatch, url):
    monkeypatch.setenv("POWERCONTEXT_PI_BASE_URL", OLD_URL)

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "pi", "--configure-only", "--server-url", url])

    if url == NEW_URL:
        assert result.exit_code == 3
        assert "Connection configuration needs attention" in result.stdout
        assert "Connection health: not checked" in result.stdout
        assert OLD_URL in result.stdout
    else:
        assert result.exit_code == 1
        assert "Connection configuration failed" in result.stderr
        assert "prepare:" in result.stderr
