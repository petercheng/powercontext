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

"""Port selection through the public configuration wizard."""

import pytest
from typer.testing import CliRunner

from powercontext.cli.config import app
from powercontext.cli.env_file import parse_environment


@pytest.mark.parametrize(
    ("existing_port", "answer", "selected_port"),
    [
        (None, "", 17429),
        (None, "18321", 18321),
        (8000, "", 8000),
        (8000, "18321", 18321),
        (17429, "18321", 18321),
        (18321, "18431", 18431),
        (18431, "8000", 8000),
        (None, "invalid\n0\n65536\n18321", 18321),
    ],
)
def test_local_port_selection_updates_saved_connections(tmp_path, existing_port, answer, selected_port):
    output = tmp_path / "server.env"
    if existing_port is None:
        answers = f"local\nbase\nn\n{answer}\nclaude-code\ndefault\nnone\ny\n"
    else:
        output.write_text(
            "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n"
            f"POWERCONTEXT_SERVER_HTTP_PORT={existing_port}\n"
            f"POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:{existing_port}\n"
            f"POWERCONTEXT_CLAUDE_SERVER_URL=http://localhost:{existing_port}/\n"
            "POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS=false\n"
            "POWERCONTEXT_PI_BASE_URL=https://proxy.example/powercontext\n"
        )
        answers = f"edit\nnetwork\nlocal\nn\n{answer}\ndone\ny\n"

    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\n" + answers,
    )

    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert values["POWERCONTEXT_SERVER_HTTP_PORT"] == str(selected_port)
    assert values["POWERCONTEXT_CLIENT_SERVER_URL"] == f"http://127.0.0.1:{selected_port}"
    assert f":{selected_port}" in values["POWERCONTEXT_CLAUDE_SERVER_URL"]
    assert f"Server port [{existing_port or 17429}]" in result.output
    assert "Dashboard, HTTP API, and MCP share" in result.output
    assert "MCP endpoint: http://127.0.0.1:" + str(selected_port) + "/mcp" in result.output
    assert "restart it with the saved configuration" in result.output
    assert "service install --env-file" in output.with_name("server.env.next-steps.md").read_text()
    if existing_port is not None:
        assert values["POWERCONTEXT_PI_BASE_URL"] == "https://proxy.example/powercontext"
        assert values["POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS"] == "false"
