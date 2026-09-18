import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest


APP_DIR = Path(__file__).resolve().parents[1]


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _request(url, method="GET", token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode())


def _start_server(dev_mode):
    port = _free_port()
    token = "test-api-token"
    env = os.environ.copy()
    env.update(
        {
            "DEV_MODE": "true" if dev_mode else "false",
            "API_TOKEN": token,
            "SUPERVISOR_TOKEN": "test-supervisor-token",
        }
    )
    if dev_mode:
        env["SUPERVISOR_BASE_URL"] = f"http://127.0.0.1:{port}"
    else:
        env.pop("SUPERVISOR_BASE_URL", None)

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=APP_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            _request(f"{base_url}/health")
            return process, base_url, token
        except Exception:
            if process.poll() is not None:
                break
            time.sleep(0.1)

    process.terminate()
    process.wait(timeout=5)
    raise RuntimeError("Guardian test server did not start")


@pytest.fixture(scope="module")
def development_server():
    process, base_url, token = _start_server(dev_mode=True)
    try:
        yield base_url, token
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_all_preview_scenarios_through_real_http_server(development_server):
    base_url, token = development_server
    expected = {
        "healthy": [],
        "device_dropout": ["dropout_device_device-kitchen"],
        "correlated_dropout": ["correlated_dropout_"],
        "missed_automation": ["missed_automation_light.hallway"],
    }

    status, catalog = _request(f"{base_url}/dev/scenarios", token=token)
    assert status == 200
    assert {item["id"] for item in catalog["scenarios"]} == set(expected)

    status, debug_payload = _request(f"{base_url}/debug/env", token=token)
    assert status == 200
    assert debug_payload["dev_mode"] is True
    assert "test-supervisor-token" not in json.dumps(debug_payload)
    assert token not in json.dumps(debug_payload)

    for scenario, expected_ids in expected.items():
        status, _ = _request(
            f"{base_url}/dev/scenarios/{scenario}",
            method="POST",
            token=token,
        )
        assert status == 200

        status, payload = _request(f"{base_url}/issues", token=token)
        assert status == 200
        issue_ids = [issue["id"] for issue in payload["issues"]]
        if scenario == "correlated_dropout":
            assert len(issue_ids) == 1
            assert issue_ids[0].startswith(expected_ids[0])
        else:
            assert issue_ids == expected_ids


def test_production_server_hides_development_routes_and_secret_values():
    process, base_url, token = _start_server(dev_mode=False)
    try:
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            _request(f"{base_url}/debug/env")
        assert unauthorized.value.code == 401

        with pytest.raises(urllib.error.HTTPError) as missing_route:
            _request(f"{base_url}/dev/scenarios", token=token)
        assert missing_route.value.code == 404

        with pytest.raises(urllib.error.HTTPError) as hidden_debug_route:
            _request(f"{base_url}/debug/env", token=token)
        assert hidden_debug_route.value.code == 404
    finally:
        process.terminate()
        process.wait(timeout=5)