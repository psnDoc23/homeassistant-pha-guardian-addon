# server.py
import hmac
import os
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Request, Body, HTTPException
from fastapi.responses import JSONResponse

from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
DEV_MODE = os.environ.get("DEV_MODE", "false").lower() == "true"

import httpx


from logging_config import setup_logging
from supervisor_client import SupervisorClient

from analyzer import analyze_dropouts

from contextlib import asynccontextmanager
import asyncio

from checkers.device_dropouts import check_device_dropouts
from checkers.device_classifier import classify_devices
from check_report import CheckHistory, build_report
from connection import ConnectionStore



logger = setup_logging()


# --- Access control ---
# The token is only for callers outside Home Assistant's own interface (for example
# the website pulling data, or an HA webhook). People using Guardian from the Home
# Assistant sidebar arrive through Ingress, which Home Assistant has already signed
# them in for, so the screen works without any token.
API_TOKEN = os.environ.get("API_TOKEN", "")
INGRESS_PROXY_IP = "172.30.32.2"  # the only address Supervisor proxies Ingress from

# Website sharing is off unless the user links this app to a website account with a
# pairing code. (Older installs can still use the website_sharing option plus api_token.)
WEBSITE_SHARING_ENABLED = os.environ.get("WEBSITE_SHARING", "false").lower() == "true"

connection_store = ConnectionStore()


def linked_connection() -> dict | None:
    return connection_store.load()


def outbound_token() -> str:
    """The credential used when talking to the website."""
    linked = linked_connection()
    if linked:
        return linked["token"]
    return API_TOKEN if WEBSITE_SHARING_ENABLED else ""


def sharing_active() -> bool:
    return bool(outbound_token())


def sharing_state() -> str:
    if linked_connection():
        return "active"
    if not WEBSITE_SHARING_ENABLED:
        return "off"
    return "active" if API_TOKEN else "needs_connection"


def sharing_payload() -> dict:
    linked = linked_connection()
    return {
        "state": sharing_state(),
        "linked": bool(linked),
        "account": (linked or {}).get("account", ""),
        "linked_at": (linked or {}).get("linked_at"),
        "info_url": WEBSITE_INFO_URL,
        "link_url": WEBSITE_LINK_URL,
    }


def is_ingress_request(request: Request) -> bool:
    return bool(request.client) and request.client.host == INGRESS_PROXY_IP


def has_valid_token(request: Request) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    presented = auth[len("Bearer "):]
    accepted = [t for t in (API_TOKEN, (linked_connection() or {}).get("token")) if t]
    return any(hmac.compare_digest(presented, token) for token in accepted)


async def verify_token(request: Request, call_next):
    if DEV_MODE:
        return await call_next(request)

    # Always allow the dashboard shell and health check through
    if request.url.path in ("/", "/health"):
        return await call_next(request)

    if is_ingress_request(request) or has_valid_token(request):
        return await call_next(request)

    return JSONResponse(status_code=401, content={"error": "Unauthorized"})



supervisor = SupervisorClient()


PROPHETDATA_PUSH_URL = os.environ.get(
    "PROPHETDATA_PUSH_URL",
    "https://prophetdata.net/connect/guardian/push/"
)


# Public page on the website explaining what connecting adds (same site as the push URL).
_push_origin = urlsplit(PROPHETDATA_PUSH_URL)
_website_base = f"{_push_origin.scheme}://{_push_origin.netloc}"
WEBSITE_INFO_URL = f"{_website_base}/connect/guardian/app/"
WEBSITE_LINK_URL = f"{_website_base}/connect/guardian/link/"
WEBSITE_PAIR_URL = f"{_website_base}/connect/guardian/pair/"
WEBSITE_UNPAIR_URL = f"{_website_base}/connect/guardian/unpair/"


async def push_to_prophetdata(issues: list, device_registry: dict = None, ha_timezone: str = None) -> bool:
    """Send findings to the website. Returns True only if a push was attempted."""
    if not sharing_active():
        logger.info(f"Push skipped -- website sharing is {sharing_state()}")
        return False
    try:
        payload = {"issues": issues}
        if device_registry:
            payload["device_registry"] = device_registry
        if ha_timezone:
            payload["ha_timezone"] = ha_timezone
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                PROPHETDATA_PUSH_URL,
                json=payload,
                headers={"Authorization": f"Bearer {outbound_token()}"}
            )
            logger.info(f"Push to prophetdata.net: status={response.status_code} body={response.text[:200]}")
            return response.is_success
    except Exception as e:
        logger.error(f"Push to prophetdata.net failed: {type(e).__name__}: {repr(e)}")
        return False




async def get_ha_timezone() -> str | None:
    """Fetch the IANA timezone name from HA's config endpoint. Returns None on failure."""
    try:
        ha_config = await supervisor.get_ha_config()
        tz = ha_config.get("time_zone")
        if tz:
            logger.info(f"HA timezone: {tz}")
        return tz
    except Exception as e:
        logger.warning(f"Could not fetch HA timezone: {e}")
        return None


async def run_checks(ha_timezone: str | None = None) -> list:
    """Run all checkers and return issues. Used by both background polling and on-demand trigger."""
    from checkers.disk_space import check_disk_space
    from checkers.usb_path import check_usb_path
    from checkers.device_dropouts import check_device_dropouts
    from checkers.correlated_dropouts import check_correlated_dropouts
    from checkers.missed_automation import check_missed_automations
    from storage import load_monitored_ids

    all_issues = []
    all_issues += await check_disk_space(supervisor)
    all_issues += await check_usb_path(supervisor)
    all_issues += await check_device_dropouts(supervisor)
    all_issues += await check_correlated_dropouts(supervisor, ha_timezone=ha_timezone)

    if DEV_MODE:
        from mock_supervisor import get_dev_monitored_ids
        monitored_ids = get_dev_monitored_ids()
    else:
        monitored_ids = load_monitored_ids()
    if monitored_ids:
        automation_configs = []
        for aid in monitored_ids:
            try:
                config = await supervisor._get_core(f"/config/automation/config/{aid}")
                automation_configs.append(config)
            except Exception as e:
                # An automation we can't read is a check that didn't happen,
                # not a check that passed.
                logger.warning(f"Could not fetch automation config for ID: {aid}")
                all_issues.append({
                    "id": f"missed_automation_check_failed_{aid}",
                    "title": f"Couldn't check automation {aid}",
                    "severity": "unknown",
                    "detail": str(e),
                })
        all_issues += await check_missed_automations(supervisor, automation_configs)

    return all_issues


check_history = CheckHistory()
_check_lock = asyncio.Lock()


async def perform_check() -> tuple[list, dict]:
    """Run every check once and remember the outcome. One run at a time."""
    async with _check_lock:
        ha_timezone = await get_ha_timezone()
        all_issues = await run_checks(ha_timezone=ha_timezone)
        report = build_report(all_issues, datetime.now(timezone.utc))
        check_history.record(report)
        return all_issues, report


async def share_with_website(all_issues: list) -> bool:
    """Send results to the website, but only when the user has turned sharing on."""
    if not sharing_active():
        return False
    ha_timezone = await get_ha_timezone()
    try:
        device_registry = await classify_devices(supervisor)
    except Exception as e:
        logger.warning(f"Device classification failed, pushing without registry: {e}")
        device_registry = None
    return await push_to_prophetdata(all_issues, device_registry, ha_timezone=ha_timezone)


async def background_polling():
    # First check shortly after start so the screen has a "last checked" time.
    delay = 5
    while True:
        await asyncio.sleep(delay)
        delay = 120
        try:
            all_issues, report = await perform_check()
            logger.info(
                f"Background poll: status={report['status']} "
                f"findings={report['finding_count']} "
                f"failed_checks={len(report['failed_checks'])}"
            )
            await share_with_website(all_issues)
        except Exception as e:
            logger.error(f"Background poll error: {e}")



@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(background_polling())
    yield

app = FastAPI(lifespan=lifespan)


from starlette.middleware.base import BaseHTTPMiddleware
app.add_middleware(BaseHTTPMiddleware, dispatch=verify_token)



logger.info(f"DEV_MODE: {DEV_MODE}")



if DEV_MODE:
    from mock_supervisor import router as mock_supervisor_router
    app.include_router(mock_supervisor_router)
    logger.info("DEV_MODE enabled -- mock supervisor routes loaded")
else:
    logger.info("Production mode -- using real Supervisor")


def ensure_dev_mode():
    if not DEV_MODE:
        raise HTTPException(status_code=404, detail="Not found")



@app.get("/debug/ingress")
async def debug_ingress(request: Request):
    ensure_dev_mode()
    return {
        "x_ingress_path": request.headers.get("x-ingress-path", "NOT FOUND"),
        "authorization_present": bool(request.headers.get("authorization")),
    }


@app.get("/debug/history/{entity_id}")
async def debug_history(entity_id: str):
    ensure_dev_mode()
    try:
        entries = await supervisor.get_history(entity_id, hours=24)
        states = [e.get("state") for e in entries]
        return {
            "entity_id": entity_id,
            "total_entries": len(entries),
            "states_found": states
        }
    except Exception as e:
        return {"error": str(e)}
    

    
@app.get("/debug/raw-history/{entity_id}")
async def debug_raw_history(entity_id: str):
    ensure_dev_mode()
    try:
        from datetime import datetime, timezone, timedelta
        start = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        data = await supervisor._get_core(
            f"/history/period/{start}?filter_entity_id={entity_id}"
        )
        return {"raw": data}
    except Exception as e:
        return {"error": str(e)}



# ---------------------------
# On-Demand Trigger Endpoint
# ---------------------------
@app.post("/trigger")
async def trigger_check():
    """
    Called by HA webhook automation to trigger an immediate check and push.
    This is the demand side of the PULL flow:
    prophetdata.net -> HA webhook -> this endpoint -> push back to prophetdata.net
    """
    logger.info("On-demand trigger received -- running checks now")
    try:
        all_issues, report = await perform_check()
        pushed = await share_with_website(all_issues)
        logger.info(f"On-demand trigger complete -- {len(all_issues)} result(s), pushed={pushed}")
        return {
            "status": "ok",
            "issues_found": len(all_issues),
            "pushed": pushed,
            "report": report,
        }
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
        "dev_mode": DEV_MODE,
    })


# ---------------------------
# Retrieve vitals Endpoint 
# ---------------------------
@app.get("/debug/env")
async def debug_env():
    ensure_dev_mode()
    return {
        "supervisor_token_present": bool(os.environ.get("SUPERVISOR_TOKEN")),
        "hassio_token_present": bool(os.environ.get("HASSIO_TOKEN")),
        "guardian_ip_present": bool(os.environ.get("GUARDIAN_IP")),
        "dev_mode": DEV_MODE,
    }


@app.get("/debug/host-info")
async def debug_host_info():
    ensure_dev_mode()
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
    if DEV_MODE:
        from mock_supervisor import get_dev_monitored_ids
        return {"monitored_automation_ids": get_dev_monitored_ids()}
    return {"monitored_automation_ids": load_monitored_ids()}


@app.post("/automations/monitored")
async def set_monitored(payload: dict):
    from storage import save_monitored_ids
    ids = payload.get("monitored_automation_ids", [])
    if DEV_MODE:
        from mock_supervisor import set_dev_monitored_ids
        set_dev_monitored_ids(ids)
    else:
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
    """Run every check now. Also used by the screen's "Check now" button.

    `issues` keeps its original shape (findings plus any check that failed) so
    existing callers keep working; `report` says whether the result can be trusted.
    """
    logger.info({"event": "issues_requested"})
    all_issues, report = await perform_check()
    return {
        "issues": all_issues,
        "report": report,
        "history": check_history.snapshot(),
        "sharing": sharing_payload(),
    }



# ---------------------------
# Website link (pairing code)
# ---------------------------
@app.post("/website/connect")
async def website_connect(payload: dict = Body(...)):
    """Trade the code from the website for this app's own credential."""
    code = str(payload.get("code", "")).strip()
    if not code:
        return JSONResponse(status_code=400, content={"error": "Enter the code from the website."})
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(WEBSITE_PAIR_URL, json={"code": code})
    except httpx.HTTPError as e:
        logger.error(f"Website pairing request failed: {type(e).__name__}")
        return JSONResponse(
            status_code=502,
            content={"error": "Couldn't reach the website. Check your internet connection and try again."},
        )

    if response.status_code in (400, 429):
        try:
            message = response.json().get("error")
        except ValueError:
            message = None
        return JSONResponse(
            status_code=response.status_code,
            content={"error": message or "The website did not accept that code."},
        )
    if not response.is_success:
        return JSONResponse(
            status_code=502,
            content={"error": "The website had a problem. Try again in a few minutes."},
        )

    try:
        data = response.json()
        token = data["token"]
    except (ValueError, KeyError, TypeError):
        return JSONResponse(
            status_code=502,
            content={"error": "The website sent an unexpected answer. Try again in a few minutes."},
        )

    connection_store.save(token, account=data.get("account", ""))
    logger.info("Linked to a website account")
    return {"status": "ok", "sharing": sharing_payload()}


@app.post("/website/disconnect")
async def website_disconnect():
    """Stop sharing now. Also tells the website to cancel this app's credential."""
    linked = linked_connection()
    revoked_on_website = False
    if linked:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    WEBSITE_UNPAIR_URL,
                    headers={"Authorization": f"Bearer {linked['token']}"},
                )
            revoked_on_website = response.is_success
        except httpx.HTTPError as e:
            logger.warning(f"Could not tell the website about the disconnect: {type(e).__name__}")
    # Local disconnect always happens, even if the website could not be reached.
    connection_store.clear()
    logger.info(f"Disconnected from the website (website notified: {revoked_on_website})")
    return {
        "status": "ok",
        "revoked_on_website": revoked_on_website,
        "sharing": sharing_payload(),
    }


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
