# server.py
import os
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from logging_config import setup_logging
from guardian_client import GuardianClient
from supervisor_client import SupervisorClient
from mock_supervisor import router as mock_supervisor_router


logger = setup_logging()
app = FastAPI()
app.include_router(mock_supervisor_router)

supervisor = SupervisorClient()


@app.get("/ha/info")
async def ha_info():
    logger.info({"event": "ha_info_requested"})
    try:
        data = await supervisor._get("/core/info")
        return data
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    

# Configure Guardian client (placeholder IP for now)
GUARDIAN_IP = os.environ.get("GUARDIAN_IP", "http://10.0.0.140")
guardian = GuardianClient(base_url=GUARDIAN_IP)


# ---------------------------
# Health Endpoint
# ---------------------------
@app.get("/health")
async def health():
    logger.info({"event": "health_check"})
    return {"status": "ok"}


# ---------------------------
# Issues Endpoint (static for now)
# ---------------------------
@app.get("/issues")
async def issues():
    logger.info({"event": "issues_requested"})
    return {
        "issues": [
            {"id": 1, "title": "Example issue", "severity": "low"},
            {"id": 2, "title": "Another issue", "severity": "medium"},
        ]
    }


# ---------------------------
# Guardian Ping
# ---------------------------
@app.get("/guardian/ping")
async def guardian_ping():
    logger.info({"event": "guardian_ping_requested"})
    return await guardian.ping()


# ---------------------------
# Supervisor Test (HA only)
# ---------------------------
@app.get("/supervisor-test")
async def supervisor_test():
    logger.info({"event": "supervisor_test_requested"})

    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        logger.error("Supervisor token not found")
        return JSONResponse(status_code=500, content={"error": "No token"})

    try:
        import urllib.request

        req = urllib.request.Request(
            "http://supervisor/info",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req) as response:
            data = response.read().decode("utf-8")
            return JSONResponse(content=json.loads(data))

    except Exception as e:
        logger.error(f"Supervisor API error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/ha/logs")
async def ha_logs():
    logger.info({"event": "ha_logs_requested"})
    try:
        data = await supervisor._get("/core/logs")
        return data
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    
    

# ---------------------------
# Uvicorn Entrypoint
# ---------------------------
if __name__ == "__main__":
    import uvicorn

    logger.info("Starting FastAPI Guardian server on port 8099")
    uvicorn.run(app, host="0.0.0.0", port=8099)