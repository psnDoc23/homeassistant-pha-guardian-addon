import asyncio

import pytest

from supervisor_client import SupervisorClient


def test_development_mode_rejects_real_supervisor(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.setenv("SUPERVISOR_BASE_URL", "http://supervisor")

    with pytest.raises(RuntimeError, match="loopback"):
        SupervisorClient()


def test_production_mode_rejects_development_mock(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("SUPERVISOR_BASE_URL", "http://127.0.0.1:8099")

    with pytest.raises(RuntimeError, match="Production mode"):
        SupervisorClient()


def test_development_mode_defaults_to_local_mock(monkeypatch):
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.delenv("SUPERVISOR_BASE_URL", raising=False)

    client = SupervisorClient()
    try:
        assert client.base_url == "http://127.0.0.1:8099"
    finally:
        asyncio.run(client.client.aclose())