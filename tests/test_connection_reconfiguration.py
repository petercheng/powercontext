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
        ["setup", host, "--configure-only", "--server-url", NEW_URL, "--json"],
    )

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["status"] == "applied"
    assert report["server_url"] == NEW_URL
    assert report["effective_server_url"] == NEW_URL
    assert load_client_settings(host)["server_url"] == NEW_URL
    if host == "codex":
        native = json.loads((tmp_path / "CODEX_HOME/plugins/cache/test/powercontext/1.0/.mcp.json").read_text())
        assert native["mcpServers"]["powercontext"]["url"] == NEW_URL + "/mcp"
    elif host == "claude-code":
        native = json.loads((tmp_path / "CLAUDE_CONFIG_DIR/settings.json").read_text())
        options = native["pluginConfigs"]["powercontext@powercontext"]["options"]
        assert options["server_url"] == NEW_URL
        assert options["capture_prompts"] is False
    elif host == "hermes":
        native = json.loads((tmp_path / "HERMES_HOME/powercontext/config.json").read_text())
        assert native["base_url"] == NEW_URL
        assert native["capture"] is False
    elif host == "openclaw":
        native = json.loads((tmp_path / "OPENCLAW_STATE_DIR/openclaw.json").read_text())
        config = native["plugins"]["entries"]["memory-powercontext"]["config"]
        assert config["endpoint"] == NEW_URL
        assert config["autoCapture"] is False
    elif host == "workbuddy":
        native = json.loads((tmp_path / "WORKBUDDY_HOME/mcp.json").read_text())
        assert native["mcpServers"]["powercontext"]["url"] == f"${{POWERCONTEXT_WORKBUDDY_SERVER_URL:-{NEW_URL}}}/mcp"
        assert native["mcpServers"]["other"]["url"] == "https://other.example/mcp"


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
        ["setup", "workbuddy", "--configure-only", "--server-url", NEW_URL, "--json"],
    )

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["status"] == "failed"
    assert native.read_bytes() == original
    assert not client_config_file().exists()


def test_configure_only_reports_an_environment_override(monkeypatch):
    monkeypatch.setenv("POWERCONTEXT_PI_BASE_URL", OLD_URL)
    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "pi", "--configure-only", "--server-url", NEW_URL, "--json"],
    )

    assert result.exit_code == 3, result.output
    report = json.loads(result.stdout)
    assert report["status"] == "needs_attention"
    assert report["effective_server_url"] == OLD_URL
    assert report["warnings"]
    assert load_client_settings("pi")["server_url"] == NEW_URL


def test_configure_only_reports_when_the_environment_blocks_saved_http_consent(monkeypatch):
    monkeypatch.setenv("POWERCONTEXT_PI_ALLOW_INSECURE_HTTP", "false")

    result = CliRunner().invoke(
        create_cli([setup_app]),
        ["setup", "pi", "--configure-only", "--server-url", "http://memory.example", "--allow-insecure-http", "--json"],
    )

    assert result.exit_code == 3, result.output
    report = json.loads(result.stdout)
    assert report["status"] == "needs_attention"
    assert any("HTTP" in warning for warning in report["warnings"])
    assert load_client_settings("pi")["allow_insecure_http"] is True


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
        ["setup", "codex", "--configure-only", "--server-url", NEW_URL, "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["effective_server_url"] == NEW_URL
    assert inactive.read_bytes() == original


def test_invalid_endpoint_is_reported_without_writing():
    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "pi", "--configure-only", "--server-url", "invalid", "--json"]
    )

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["status"] == "failed"
    assert report["error"]
    assert not client_config_file().exists()


def test_unavailable_native_configuration_is_reported_as_json(tmp_path):
    (tmp_path / "WORKBUDDY_HOME/mcp.json").unlink()

    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "workbuddy", "--configure-only", "--server-url", NEW_URL, "--json"]
    )

    assert result.exit_code == 1
    report = json.loads(result.stdout)
    assert report["status"] == "failed"
    assert "Install" in report["error"]
    assert not client_config_file().exists()


def test_invalid_client_configuration_is_not_overwritten(tmp_path):
    client_config_file().write_text("invalid-json")
    native = tmp_path / "WORKBUDDY_HOME/mcp.json"
    original = native.read_bytes()

    result = CliRunner().invoke(
        create_cli([setup_app]), ["setup", "workbuddy", "--configure-only", "--server-url", NEW_URL, "--json"]
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "failed"
    assert client_config_file().read_text() == "invalid-json"
    assert native.read_bytes() == original
