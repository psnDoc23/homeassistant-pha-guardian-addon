"""Guardian must be useful with no website connection, and must not over-claim."""

import asyncio

import pytest
from fastapi.testclient import TestClient

import server
from check_report import (
    STATUS_ATTENTION,
    STATUS_INCOMPLETE,
    STATUS_OK,
    CheckHistory,
    build_report,
)
from connection import ConnectionStore
from dev_scenarios import ScenarioSupervisor


FINDING = {"id": "disk_space_low", "title": "Low disk space", "severity": "high"}
FAILURE = {
    "id": "usb_path_check_failed",
    "title": "USB path check failed",
    "severity": "unknown",
    "detail": "boom",
}


# --- report: "healthy" must be earned ---------------------------------------

def test_nothing_found_and_everything_finished_is_ok():
    report = build_report([])
    assert report["status"] == STATUS_OK
    assert report["all_checks_completed"] is True


def test_a_check_that_could_not_finish_is_never_reported_as_healthy():
    report = build_report([FAILURE])
    assert report["status"] == STATUS_INCOMPLETE
    assert report["finding_count"] == 0
    assert report["all_checks_completed"] is False
    assert report["failed_checks"][0]["title"] == "USB path check failed"


def test_findings_win_over_failures_but_failures_are_still_listed():
    report = build_report([FINDING, FAILURE])
    assert report["status"] == STATUS_ATTENTION
    assert report["finding_count"] == 1
    assert len(report["failed_checks"]) == 1


def test_last_complete_check_only_advances_when_everything_finished():
    history = CheckHistory()
    history.record(build_report([]))
    first_complete = history.last_complete_at
    assert first_complete is not None

    failed = build_report([FAILURE])
    failed["checked_at"] = "2099-01-01T00:00:00+00:00"
    history.record(failed)

    assert history.last_attempt_at == "2099-01-01T00:00:00+00:00"
    assert history.last_complete_at == first_complete


# --- access: screen works from Home Assistant, strangers need the token -----

@pytest.fixture
def production(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "connection_store", ConnectionStore(tmp_path))
    monkeypatch.setattr(server, "DEV_MODE", False)
    monkeypatch.setattr(server, "API_TOKEN", "")
    monkeypatch.setattr(server, "WEBSITE_SHARING_ENABLED", False)
    monkeypatch.setattr(server, "supervisor", ScenarioSupervisor("healthy"))
    monkeypatch.setattr(server, "check_history", CheckHistory())


def _client(host):
    return TestClient(server.app, client=(host, 50000))


def test_ingress_requests_work_with_no_token_configured(production):
    response = _client(server.INGRESS_PROXY_IP).get("/issues")
    assert response.status_code == 200
    body = response.json()
    assert body["report"]["status"] == STATUS_OK
    assert body["sharing"]["state"] == "off"
    assert body["sharing"]["info_url"].endswith("/connect/guardian/app/")


def test_other_callers_are_rejected_when_no_token_is_configured(production):
    assert _client("10.0.0.50").get("/issues").status_code == 401


def test_other_callers_need_the_exact_token_when_one_is_configured(production, monkeypatch):
    monkeypatch.setattr(server, "API_TOKEN", "secret")
    client = _client("10.0.0.50")
    assert client.get("/issues").status_code == 401
    assert client.get("/issues", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/issues", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_failed_check_is_reported_through_the_real_endpoint(production, monkeypatch):
    monkeypatch.setattr(server, "supervisor", ScenarioSupervisor("checks_cannot_finish"))
    body = _client(server.INGRESS_PROXY_IP).get("/issues").json()
    assert body["report"]["status"] == STATUS_INCOMPLETE
    assert body["history"]["last_complete_at"] is None
    assert body["report"]["failed_checks"][0]["id"] == "disk_space_check_failed"


# --- website sharing: nothing leaves unless it is switched on ---------------

class _ExplodingClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("Guardian tried to contact the website")


@pytest.mark.parametrize(
    "enabled, token, expected_state",
    [
        (False, "", "off"),
        (False, "secret", "off"),  # a token alone must not start sharing
        (True, "", "needs_connection"),
    ],
)
def test_nothing_is_sent_unless_sharing_is_on_and_connected(
    production, monkeypatch, enabled, token, expected_state
):
    monkeypatch.setattr(server, "WEBSITE_SHARING_ENABLED", enabled)
    monkeypatch.setattr(server, "API_TOKEN", token)
    monkeypatch.setattr(server.httpx, "AsyncClient", _ExplodingClient)

    assert server.sharing_state() == expected_state
    assert asyncio.run(server.share_with_website([FINDING])) is False
    assert asyncio.run(server.push_to_prophetdata([FINDING])) is False


def test_trigger_checks_locally_and_reports_nothing_was_pushed(production, monkeypatch):
    monkeypatch.setattr(server.httpx, "AsyncClient", _ExplodingClient)
    response = _client(server.INGRESS_PROXY_IP).post("/trigger")
    assert response.status_code == 200
    assert response.json()["pushed"] is False
