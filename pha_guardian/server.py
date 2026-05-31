# server.py
import os
from fastapi import FastAPI, Request, Body
from fastapi.responses import JSONResponse

from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="/app/templates")

import httpx


from logging_config import setup_logging
from supervisor_client import SupervisorClient

from analyzer import analyze_dropouts

from contextlib import asynccontextmanager
import asyncio





logger = setup_logging()


# --- Token auth middleware --- https://claude.ai/chat/6b3db029-b5d8-4150-a0e9-e5820eed706c
API_TOKEN = os.environ.get("API_TOKEN", "")

async def verify_token(request: Request, call_next):
    # Always allow the dashboard and health check through
    if request.url.path in ("/", "/health"):
        return await call_next(request)

    if not API_TOKEN:
        # Token not configured — pass through (local-only mode)
        return await call_next(request)

    auth = request.headers.get("Authorization", "")
    # logger.info(f"Auth header received: '{auth}'")  # debug ONLY - don't be dropping tokens in logs 
    if not auth.startswith("Bearer ") or auth[len("Bearer "):] != API_TOKEN:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    return await call_next(request)



supervisor = SupervisorClient()


PROPHETDATA_PUSH_URL = os.environ.get(
    "PROPHETDATA_PUSH_URL",
    "https://prophetdata.net/connect/guardian/push/"
)

async def push_to_prophetdata(issues: list):
    if not API_TOKEN:
        logger.info("Push skipped — no API token configured")
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                PROPHETDATA_PUSH_URL,
                json={"issues": issues},
                headers={"Authorization": f"Bearer {API_TOKEN}"}
            )
            logger.info(f"Push to prophetdata.net: {response.status_code}")
    except Exception as e:
        logger.error(f"Push to prophetdata.net failed: {e}")


async def run_checks() -> list:
    """Run all checkers and return issues. Used by both background polling and on-demand trigger."""
    from checkers.disk_space import check_disk_space
    from checkers.usb_path import check_usb_path
    from checkers.missed_automation import check_missed_automations
    from storage import load_monitored_ids

    all_issues = []
    all_issues += await check_disk_space(supervisor)
    all_issues += await check_usb_path(supervisor)

    monitored_ids = load_monitored_ids()
    if monitored_ids:
        automation_configs = []
        for aid in monitored_ids:
            try:
                config = await supervisor._get_core(f"/config/automation/config/{aid}")
                automation_configs.append(config)
            except Exception:
                pass
        all_issues += await check_missed_automations(supervisor, automation_configs)

    return all_issues



async def background_polling():
    while True:
        await asyncio.sleep(300)
        try:
            all_issues = await run_checks()

            if all_issues:
                logger.info(f"Background poll found {len(all_issues)} issue(s)")
            else:
                logger.info("Background poll: no issues found")

            await push_to_prophetdata(all_issues)

        except Exception as e:
            logger.error(f"Background poll error: {e}")



@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(background_polling())
    yield

app = FastAPI(lifespan=lifespan)


# https://claude.ai/chat/6b3db029-b5d8-4150-a0e9-e5820eed706c
from starlette.middleware.base import BaseHTTPMiddleware
app.add_middleware(BaseHTTPMiddleware, dispatch=verify_token)



# determine whether in dev mode or not
DEV_MODE = os.environ.get("DEV_MODE", "false").lower() == "true"
logger.info(f"DEV_MODE: {DEV_MODE}")



if DEV_MODE:
    from mock_supervisor import router as mock_supervisor_router
    app.include_router(mock_supervisor_router)
    logger.info("DEV_MODE enabled — mock supervisor routes loaded")
else:
    logger.info("Production mode — using real Supervisor")



@app.get("/debug/ingress")
async def debug_ingress(request: Request):
    return {
        "x_ingress_path": request.headers.get("x-ingress-path", "NOT FOUND"),
        "headers": dict(request.headers)
    }


# ---------------------------
# On-Demand Trigger Endpoint
# ---------------------------
@app.post("/trigger")
async def trigger_check():
    """
    Called by HA webhook automation to trigger an immediate check and push.
    This is the demand side of the PULL flow:
    prophetdata.net → HA webhook → this endpoint → push back to prophetdata.net
    """
    logger.info("On-demand trigger received — running checks now")
    try:
        all_issues = await run_checks()
        await push_to_prophetdata(all_issues)
        logger.info(f"On-demand trigger complete — {len(all_issues)} issue(s) pushed")
        return {"status": "ok", "issues_found": len(all_issues)}
    except Exception as e:
        logger.error(f"Trigger error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    

    
# ---------------------------
# Dashboard Endpoint 
# ---------------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    ingress_path = request.headers.get("x-ingress-path", "")
    return templates.TemplateResponse(request, "dashboard.html", {
        "ingress_path": ingress_path,
        "api_token": API_TOKEN
    })


# ---------------------------
# Retrieve vitals Endpoint 
# ---------------------------
@app.get("/debug/env")
async def debug_env():
    return {
        "SUPERVISOR_TOKEN": os.environ.get("SUPERVISOR_TOKEN", "MISSING"),
        "HASSIO_TOKEN": os.environ.get("HASSIO_TOKEN", "MISSING"),
        "GUARDIAN_IP": os.environ.get("GUARDIAN_IP", "MISSING"),
    }


# https://claude.ai/chat/c79fd47a-db84-47cc-a903-1bebebaa38e9
@app.get("/debug/host-info")
async def debug_host_info():
    return await supervisor._get("/host/info")



# automation candidates
@app.get("/ha/automations/candidates")
async def automation_candidates():
    from checkers.automation_scanner import scan_automations
    return await scan_automations(supervisor)


# more automations
@app.get("/automations/monitored")
async def get_monitored():
    from storage import load_monitored_ids
    return {"monitored_automation_ids": load_monitored_ids()}


@app.post("/automations/monitored")
async def set_monitored(payload: dict):
    from storage import save_monitored_ids
    ids = payload.get("monitored_automation_ids", [])
    save_monitored_ids(ids)
    return {"status": "ok", "monitored_automation_ids": ids}



@app.post("/ha/automations/{automation_id}/enable")
async def enable_automation(automation_id: str):
    try:
        # Look up the full entity_id from the automation ID
        states = await supervisor._get_core("/states")
        auto_state = next(
            (s for s in states 
             if s.get("attributes", {}).get("id") == automation_id),
            None
        )
        if not auto_state:
            return JSONResponse(status_code=404, content={"error": "Automation not found"})
        
        entity_id = auto_state.get("entity_id")
        await supervisor._post_core("/services/automation/turn_on", {
            "entity_id": entity_id
        })
        return {"status": "ok"}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})



# ---------------------------
# Health Endpoint 
# ---------------------------
@app.get("/health")
async def health():
    logger.info({"event": "health_check"})
    return {"status": "ok"}


# ---------------------------
# Issues Endpoint
# ---------------------------
@app.get("/issues")
async def issues():
    logger.info({"event": "issues_requested"})
    
    from checkers.disk_space import check_disk_space
    from checkers.usb_path import check_usb_path
    from checkers.missed_automation import check_missed_automations
    from storage import load_monitored_ids

    all_issues = []
    all_issues += await check_disk_space(supervisor)
    all_issues += await check_usb_path(supervisor)

    monitored_ids = load_monitored_ids()
    if monitored_ids:
        automation_configs = []
        for aid in monitored_ids:
            try:
                config = await supervisor._get_core(f"/config/automation/config/{aid}")
                automation_configs.append(config)
            except Exception:
                logger.warning(f"Could not fetch automation config for ID: {aid}")
        all_issues += await check_missed_automations(supervisor, automation_configs)

    return {"issues": all_issues}





# ---------------------------
# Start supervisor Endpoints 
# ---------------------------
@app.get("/ha/logs")
async def ha_logs():
    logger.info({"event": "ha_logs_requested"})
    try:
        data = await supervisor._get_text("/core/logs")
        return data
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    
 
@app.get("/ha/info")
async def ha_info():
    # This now uses the clean logic inside the client
    return await supervisor._get("/core/info")


# ---------------------------
# State Endpoint 
# ---------------------------
@app.get("/ha/state/{entity_id}")
async def ha_state(entity_id: str):
    logger.info({"event": "ha_state_requested", "entity_id": entity_id})
    return await supervisor.get_state(entity_id)


# ---------------------------
# Logbook Endpoint 
# ---------------------------
@app.get("/ha/logbook/{entity_id}")
async def ha_logbook(entity_id: str, hours: int = 24):
    return await supervisor.get_logbook(entity_id, hours=hours)



# ---------------------------
# Dropout Analysis Endpoint
# ---------------------------
@app.get("/ha/analyze/dropouts/{entity_id}")
async def analyze_entity_dropouts(entity_id: str, hours: int = 24):
    entries = await supervisor.get_logbook(entity_id, hours=hours)
    return analyze_dropouts(entries)



# ---------------------------
# Uvicorn Entrypoint
# ---------------------------
if __name__ == "__main__":
    import uvicorn

    logger.info("Starting FastAPI Guardian server on port 8099")
    uvicorn.run(app, host="0.0.0.0", port=8099)