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

from typer.testing import CliRunner

from powercontext.cli.config import app as config_app
from powercontext.paths import default_server_env_file


def test_default_init_writes_user_configuration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(config_app, ["init", "--template"], input="\n")

    assert result.exit_code == 0, result.output
    assert default_server_env_file().is_file()
    assert not (tmp_path / ".env").exists()
    assert "POWERCONTEXT_SERVER_HTTP_PORT=17429" in default_server_env_file().read_text()


def test_config_show_reports_file_values_and_redacts_credentials(monkeypatch):
    path = default_server_env_file()
    path.parent.mkdir(parents=True)
    path.write_text("POWERCONTEXT_SERVER_HTTP_PORT=8000\nPOWERCONTEXT_SERVER_AUTH_TOKEN=private-token\n")
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", "18321")

    result = CliRunner().invoke(config_app, ["show", "--json"])

    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["configuration_file"] == str(path)
    assert report["settings"]["POWERCONTEXT_SERVER_HTTP_PORT"] == "8000"
    assert "private-token" not in result.output
