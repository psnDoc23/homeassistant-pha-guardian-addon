"""HTTP mock of the Home Assistant Supervisor used by Guardian Local Preview."""

import json
import os

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import PlainTextResponse

from dev_scenarios import ScenarioStore, ScenarioSupervisor


router = APIRouter()
store = ScenarioStore(os.environ.get("GUARDIAN_SCENARIO", "healthy"))
supervisor = ScenarioSupervisor(store.current_name)
supervisor.store = store


def get_dev_monitored_ids():
    return list(store.current["monitored_automation_ids"])


def set_dev_monitored_ids(ids):
    store.current["monitored_automation_ids"] = list(ids)


@router.get("/dev/scenarios")
async def list_scenarios():
    return {"current": store.current_name, "scenarios": store.summaries()}


@router.post("/dev/scenarios/{scenario_id}")
async def select_scenario(scenario_id: str):
    try:
        scenario = store.select(scenario_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown development scenario")
    return {
        "status": "ok",
        "current": store.current_name,
        "description": scenario["description"],
    }


@router.get("/core/api/states")
async def mock_states():
    return await supervisor._get_core("/states")


@router.get("/core/api/states/{entity_id:path}")
async def mock_state(entity_id: str):
    return await supervisor._get_core(f"/states/{entity_id}")


@router.get("/core/api/config")
async def mock_config():
    return await supervisor._get_core("/config")


@router.get("/core/api/config/entity_registry/list")
async def mock_entity_registry():
    return await supervisor._get_core("/config/entity_registry/list")


@router.get("/core/api/config/device_registry/list")
async def mock_device_registry():
    return await supervisor._get_core("/config/device_registry/list")


@router.get("/core/api/config/automation/config/{automation_id}")
async def mock_automation_config(automation_id: str):
    return await supervisor._get_core(f"/config/automation/config/{automation_id}")


@router.get("/core/api/history/period/{start}")
async def mock_history(start: str, filter_entity_id: str = Query("")):
    entries = await supervisor.get_history(filter_entity_id, hours=48)
    return [
        [
            {
                "entity_id": entry["entity_id"],
                "state": entry["state"],
                "last_changed": entry["when"],
            }
            for entry in entries
        ]
    ]


@router.get("/core/api/logbook/{start}")
async def mock_logbook(start: str, entity_id: str = Query("")):
    return await supervisor.get_logbook(entity_id, hours=24)


@router.post("/core/api/template", response_class=PlainTextResponse)
async def mock_template(payload: dict = Body(default={})):
    return await supervisor._post_core_text("/template", payload)


@router.post("/core/api/services/{domain}/{service}")
async def mock_service(domain: str, service: str, payload: dict = Body(default={})):
    return {"status": "ok", "domain": domain, "service": service, "payload": payload}


@router.get("/host/info")
async def mock_host_info():
    return await supervisor._get("/host/info")


@router.get("/core/info")
async def mock_core_info():
    return await supervisor._get("/core/info")


@router.get("/core/logs", response_class=PlainTextResponse)
async def mock_logs():
    return store.current["logs"]