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

"""CLI acceptance for changing a Server endpoint while retaining project data."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from shutil import which
from time import monotonic, sleep

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from powercontext.cli.env_file import parse_environment
from powercontext.paths import default_server_env_file


def _cli(executable: str, directory: Path, *arguments: str) -> dict[str, object]:
    result = subprocess.run(
        [executable, *arguments], cwd=directory, capture_output=True, text=True, check=False, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@contextmanager
def _server(executable: str, directory: Path, endpoint: str):
    log_path = directory / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen([executable, "server", "run"], cwd=directory, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = monotonic() + 30
            with httpx.Client(timeout=0.5, trust_env=False) as client:
                while process.poll() is None and monotonic() < deadline:
                    try:
                        if client.get(endpoint + "/health/live").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    sleep(0.1)
                else:
                    pytest.fail(f"Server did not become live: {log_path.read_text()}")
                yield client
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_changing_port_and_working_directory_preserves_data_and_reconnects_client(
    tmp_path, monkeypatch, free_tcp_port_factory
):
    executable = which("powercontext")
    assert executable is not None
    for name in os.environ:
        if name.startswith("POWERCONTEXT_"):
            monkeypatch.delenv(name)
    for name in ("HOME", "USERPROFILE", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "PI_CODING_AGENT_DIR"):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setenv(name, str(directory))
    client_file = tmp_path / "clients.json"
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(client_file))
    environment = default_server_env_file()
    environment.parent.mkdir(parents=True, exist_ok=True)
    scope = None

    for directory_name in ("first-project", "second-project"):
        directory = tmp_path / directory_name
        directory.mkdir()
        port = free_tcp_port_factory()
        endpoint = f"http://127.0.0.1:{port}"
        answers = (
            f"sqlite\n\nlocal\nbase\ny\n{port}\npi\ndefault\nnone\ny\n"
            if scope is None
            else f"sqlite\n\nedit\nnetwork\nlocal\n\n{port}\ndone\ny\n"
        )
        configured = subprocess.run(
            [executable, "config", "init", "--language", "en"],
            cwd=directory,
            input=answers,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert configured.returncode == 0, configured.stdout + configured.stderr
        values = parse_environment(environment.read_text())
        assert values["POWERCONTEXT_SERVER_HTTP_PORT"] == str(port)
        assert values["POWERCONTEXT_PI_BASE_URL"] == endpoint
        assert f"Dashboard: {endpoint}/dashboard/home" in configured.stdout
        assert f"MCP endpoint: {endpoint}/mcp" in configured.stdout
        token = values["POWERCONTEXT_CLIENT_API_TOKEN"]
        monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", token)
        (directory / ".env").write_text("POWERCONTEXT_SERVER_HTTP_PORT=1\n")
        with _server(executable, directory, endpoint) as http:
            http.headers["Authorization"] = f"Bearer {token}"
            assert http.get(endpoint + "/dashboard/home", timeout=5).status_code == 200

            async def verify_mcp(url=endpoint, authorization=f"Bearer {token}"):
                async with Client(
                    StreamableHttpTransport(url + "/mcp", headers={"Authorization": authorization})
                ) as mcp:
                    await mcp.ping()

            asyncio.run(verify_mcp())
            report = _cli(executable, directory, "setup", "pi", "--configure-only", "--server-url", endpoint, "--json")
            assert report["status"] == "applied"
            saved_url = json.loads(client_file.read_text())["hosts"]["pi"]["server_url"]
            assert saved_url == endpoint
            if scope is None:
                response = http.post(
                    saved_url + "/v1/scopes",
                    json={
                        "title": "Persistent connection",
                        "summary": "Survives port and directory changes",
                        "idempotency_key": "persistent-connection",
                    },
                )
                response.raise_for_status()
                scope = response.json()
            else:
                response = http.get(saved_url + "/v1/scopes/" + scope["scope_id"])
                response.raise_for_status()
                fetched = response.json()
                assert fetched["scope_id"] == scope["scope_id"]
                assert fetched["title"] == scope["title"]
                assert fetched["summary"] == scope["summary"]
