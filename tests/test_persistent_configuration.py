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

"""Persistent configuration remains independent of the invoking working directory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from powercontext.cli.config import app as config_app
from powercontext.client.transport_policy import client_config_file, resolve_client_transport
from powercontext.paths import default_server_env_file
from powercontext.server.cli import app as server_app


def test_default_init_writes_user_configuration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(config_app, ["init", "--template"], input="\n")

    assert result.exit_code == 0, result.output
    assert default_server_env_file().is_file()
    assert not (tmp_path / ".env").exists()
    assert "POWERCONTEXT_SERVER_HTTP_PORT=17429" in default_server_env_file().read_text()


def test_foreground_uses_user_configuration_from_another_directory(tmp_path, monkeypatch):
    path = default_server_env_file()
    path.parent.mkdir(parents=True)
    path.write_text("POWERCONTEXT_SERVER_HTTP_PORT=18321\n")
    observed = []
    monkeypatch.setattr("powercontext.server.cli._run_configured_server", lambda settings: observed.append(settings))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("POWERCONTEXT_SERVER_HTTP_PORT=8000\n")

    result = CliRunner().invoke(server_app, ["run"])

    assert result.exit_code == 0, result.output
    assert observed[0].http.port == 18321
    assert str(path) in result.output


def test_explicit_file_and_port_override_user_configuration(tmp_path, monkeypatch):
    path = default_server_env_file()
    path.parent.mkdir(parents=True)
    path.write_text("POWERCONTEXT_SERVER_HTTP_PORT=18321\n")
    explicit = tmp_path / "project.env"
    explicit.write_text("POWERCONTEXT_SERVER_HTTP_PORT=8000\n")
    observed = []
    monkeypatch.setattr("powercontext.server.cli._run_configured_server", lambda settings: observed.append(settings))

    result = CliRunner().invoke(server_app, ["run", "--env-file", str(explicit), "--port", "18432"])

    assert result.exit_code == 0, result.output
    assert observed[0].http.port == 18432


@pytest.mark.parametrize("port_name", ["POWERCONTEXT_SERVER_HTTP_PORT", "powercontext_server_http_port"])
def test_config_report_explains_effective_values_without_exposing_secrets(tmp_path, monkeypatch, port_name):
    path = default_server_env_file()
    path.parent.mkdir(parents=True)
    path.write_text(
        f"{port_name}=8000\nPOWERCONTEXT_SERVER_AUTH_TOKEN=private-token\nPOWERCONTEXT_SERVER_ACCESS_MODE=enforced\n"
    )
    monkeypatch.setenv(port_name, "18321")
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))

    result = CliRunner().invoke(config_app, ["show", "--json"])

    assert result.exit_code == 0, result.output
    assert "private-token" not in result.output
    report = json.loads(result.output)
    assert report["configuration_file"] == str(path)
    assert report["settings"]["POWERCONTEXT_SERVER_HTTP_PORT"] == {"value": "18321", "source": "environment"}
    assert report["settings"]["POWERCONTEXT_SERVER_DATABASE_URL"]["value"].endswith("/data/powercontext.db")


def test_config_report_identifies_an_empty_configuration_file():
    path = default_server_env_file()
    path.parent.mkdir(parents=True)
    path.touch()

    result = CliRunner().invoke(config_app, ["show", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["configuration_file"] == str(path)
    assert report["settings"]["POWERCONTEXT_SERVER_HTTP_PORT"] == {"value": "17429", "source": "default"}


@pytest.mark.parametrize("xdg", ["custom", "relative", "missing"])
def test_client_config_resolves_xdg_and_preserves_legacy_file(tmp_path, monkeypatch, xdg):
    monkeypatch.setattr("powercontext.paths.sys.platform", "linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("POWERCONTEXT_CLIENT_CONFIG_FILE")
    if xdg == "missing":
        monkeypatch.delenv("XDG_CONFIG_HOME")
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config") if xdg == "custom" else "relative")
    preferred = tmp_path / ("config" if xdg == "custom" else ".config") / "powercontext" / "clients.json"
    assert client_config_file() == preferred
    legacy = tmp_path / ".config/powercontext/clients.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"version": 1, "hosts": {"client": {"server_url": "http://127.0.0.1:8000"}}}))

    assert resolve_client_transport("client")[0] == "http://127.0.0.1:8000"
    preferred.parent.mkdir(parents=True, exist_ok=True)
    preferred.write_text(json.dumps({"version": 1, "hosts": {"client": {"server_url": "http://127.0.0.1:18321"}}}))
    assert resolve_client_transport("client")[0] == "http://127.0.0.1:18321"
