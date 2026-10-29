"""Linking the app to a website account with a pairing code."""

import json
import stat

import httpx
import pytest
from fastapi.testclient import TestClient

import server
from check_report import CheckHistory
from connection import ConnectionStore
from dev_scenarios import ScenarioSupervisor

REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture
def app_state(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "DEV_MODE", False)
    monkeypatch.setattr(server, "API_TOKEN", "")
    monkeypatch.setattr(server, "WEBSITE_SHARING_ENABLED", False)
    monkeypatch.setattr(server, "supervisor", ScenarioSupervisor("healthy"))
    monkeypatch.setattr(server, "check_history", CheckHistory())
    store = ConnectionStore(tmp_path)
    monkeypatch.setattr(server, "connection_store", store)
    return store


def ingress():
    return TestClient(server.app, client=(server.INGRESS_PROXY_IP, 50000))


def fake_website(monkeypatch, handler):
    """Route the app's outgoing website requests to `handler` instead of the internet."""
    calls = []

    def recording(request):
        calls.append(request)
        return handler(request)

    class Fake(REAL_ASYNC_CLIENT):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(recording)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(server.httpx, "AsyncClient", Fake)
    return calls


def accepting(request):
    if request.url.path.endswith("/pair/"):
        return httpx.Response(200, json={"token": "t" * 64, "account": "d***@example.com"})
    return httpx.Response(200, json={"status": "ok"})


def test_valid_code_links_the_app_and_turns_sharing_on(app_state, monkeypatch):
    calls = fake_website(monkeypatch, accepting)
    response = ingress().post("/website/connect", json={"code": "abcd-efgh"})
    assert response.status_code == 200
    sharing = response.json()["sharing"]
    assert sharing["state"] == "active"
    assert sharing["linked"] is True
    assert sharing["account"] == "d***@example.com"
    assert json.loads(calls[0].content) == {"code": "abcd-efgh"}
    assert server.sharing_active() is True
    assert server.outbound_token() == "t" * 64


def test_credential_is_stored_privately_and_never_shown_by_the_app(app_state, monkeypatch):
    fake_website(monkeypatch, accepting)
    response = ingress().post("/website/connect", json={"code": "ABCD-EFGH"})
    assert "t" * 64 not in response.text
    assert "t" * 64 not in ingress().get("/issues").text
    assert stat.S_IMODE(app_state.path.stat().st_mode) == 0o600


def test_bad_code_shows_the_websites_message_and_does_not_link(app_state, monkeypatch):
    fake_website(
        monkeypatch,
        lambda request: httpx.Response(400, json={"error": "That code isn't valid or has expired."}),
    )
    response = ingress().post("/website/connect", json={"code": "ZZZZ-ZZZZ"})
    assert response.status_code == 400
    assert "isn't valid" in response.json()["error"]
    assert app_state.load() is None
    assert server.sharing_state() == "off"


def test_unreachable_website_gives_a_plain_message_and_does_not_link(app_state, monkeypatch):
    def down(request):
        raise httpx.ConnectError("no route")

    fake_website(monkeypatch, down)
    response = ingress().post("/website/connect", json={"code": "ABCD-EFGH"})
    assert response.status_code == 502
    assert "internet" in response.json()["error"]
    assert app_state.load() is None


def test_empty_code_is_rejected_without_contacting_the_website(app_state, monkeypatch):
    calls = fake_website(monkeypatch, accepting)
    assert ingress().post("/website/connect", json={"code": "  "}).status_code == 400
    assert calls == []


def test_disconnect_clears_the_link_and_tells_the_website(app_state, monkeypatch):
    app_state.save("t" * 64, "d***@example.com")
    calls = fake_website(monkeypatch, accepting)
    response = ingress().post("/website/disconnect")
    body = response.json()
    assert body["revoked_on_website"] is True
    assert body["sharing"]["state"] == "off"
    assert calls[0].headers["Authorization"] == "Bearer " + "t" * 64
    assert app_state.load() is None
    assert server.sharing_active() is False


def test_disconnect_still_works_locally_when_the_website_is_unreachable(app_state, monkeypatch):
    app_state.save("t" * 64)

    def down(request):
        raise httpx.ConnectError("no route")

    fake_website(monkeypatch, down)
    body = ingress().post("/website/disconnect").json()
    assert body["revoked_on_website"] is False
    assert app_state.load() is None
    assert body["sharing"]["state"] == "off"


def test_nothing_is_pushed_after_disconnecting(app_state, monkeypatch):
    app_state.save("t" * 64)
    fake_website(monkeypatch, accepting)
    ingress().post("/website/disconnect")

    class Exploding:
        def __init__(self, *a, **k):
            raise AssertionError("Guardian contacted the website after disconnecting")

    monkeypatch.setattr(server.httpx, "AsyncClient", Exploding)
    import asyncio

    assert asyncio.run(server.share_with_website([])) is False


def test_linked_credential_is_accepted_from_outside_and_is_not_valid_once_cleared(app_state):
    app_state.save("linked-secret")
    outsider = TestClient(server.app, client=("10.0.0.50", 50000))
    assert outsider.get("/issues", headers={"Authorization": "Bearer linked-secret"}).status_code == 200
    app_state.clear()
    assert outsider.get("/issues", headers={"Authorization": "Bearer linked-secret"}).status_code == 401


def test_connect_endpoints_are_not_open_to_outside_callers(app_state):
    outsider = TestClient(server.app, client=("10.0.0.50", 50000))
    assert outsider.post("/website/connect", json={"code": "ABCD-EFGH"}).status_code == 401
    assert outsider.post("/website/disconnect").status_code == 401


def test_store_round_trip_and_unreadable_file_counts_as_unlinked(tmp_path):
    store = ConnectionStore(tmp_path)
    assert store.load() is None
    store.save("abc", "x***@y.com")
    assert store.load()["token"] == "abc"
    store.path.write_text("not json")
    assert store.load() is None
    with pytest.raises(ValueError):
        store.save("")


def test_report_outcome_is_recorded_for_each_kind_of_result(app_state, monkeypatch):
    app_state.save("t" * 64)
    import asyncio

    def run(handler):
        fake_website(monkeypatch, handler)
        asyncio.run(server.push_to_prophetdata([]))
        return server.sharing_payload()["last_report"]["state"]

    assert server.sharing_payload()["last_report"]["state"] == "none"
    assert run(lambda r: httpx.Response(200, json={"status": "ok"})) == "sent"
    assert run(lambda r: httpx.Response(401, json={"error": "Unauthorized"})) == "rejected"
    assert run(lambda r: httpx.Response(500)) == "failed"

    def down(request):
        raise httpx.ConnectError("no route")

    assert run(down) == "unreachable"
    assert server.sharing_payload()["last_report"]["at"]


def test_outcome_resets_when_linking_changes_and_linking_wakes_the_poller(app_state, monkeypatch):
    server.record_share_outcome("rejected")
    server.check_soon.clear()
    fake_website(monkeypatch, accepting)
    response = ingress().post("/website/connect", json={"code": "ABCD-EFGH"})
    assert response.json()["sharing"]["last_report"]["state"] == "none"
    assert server.check_soon.is_set()
    server.record_share_outcome("sent")
    body = ingress().post("/website/disconnect").json()
    assert body["sharing"]["last_report"]["state"] == "none"


def test_nothing_is_recorded_when_sharing_is_off(app_state):
    import asyncio

    assert asyncio.run(server.push_to_prophetdata([])) is False
    assert server.sharing_payload()["last_report"]["state"] == "none"


def test_sharing_info_gives_the_app_the_website_finding_address(app_state):
    sharing = server.sharing_payload()
    assert sharing["finding_url"].endswith("/connect/guardian/finding/")


def test_dashboard_offers_open_on_website_only_for_device_findings():
    html = (server.BASE_DIR / "templates" / "dashboard.html").read_text() if hasattr(server, "BASE_DIR") else None
    if html is None:
        from pathlib import Path

        html = (Path(server.__file__).parent / "templates" / "dashboard.html").read_text()
    assert "Open on the website" in html
    assert "id === 'dropout_check_failed'" in html
    assert "encodeURIComponent(issueId)" in html
