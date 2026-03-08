# server.py
import os
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from logging_config import setup_logging
from supervisor_client import SupervisorClient


logger = setup_logging()

app = FastAPI()

supervisor = SupervisorClient()


# determine whether in dev mode or not
DEV_MODE = os.environ.get("DEV_MODE", "false").lower() == "true"
logger.info(f"DEV_MODE: {DEV_MODE}")



if DEV_MODE:
    from mock_supervisor import router as mock_supervisor_router
    app.include_router(mock_supervisor_router)
    logger.info("DEV_MODE enabled — mock supervisor routes loaded")
else:
    logger.info("Production mode — using real Supervisor")


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



# ---------------------------
# Health Endpoint 
# ---------------------------
@app.get("/health")
async def health():
    logger.info({"event": "health_check"})
    return {"status": "ok"}


# ---------------------------
# Issues Endpoint (static for now)
# In the future this will be home to the real analyzed results...
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
# Uvicorn Entrypoint
# ---------------------------
if __name__ == "__main__":
    import uvicorn

    logger.info("Starting FastAPI Guardian server on port 8099")
    uvicorn.run(app, host="0.0.0.0", port=8099)