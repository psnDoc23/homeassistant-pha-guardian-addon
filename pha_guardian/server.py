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
    # This now uses the clean logic inside the client
    return await supervisor._get("/core/info")
    

# 1. Initialize as None so the variable exists in the namespace
guardian = None

# 2. Get the IP from the environment
GUARDIAN_IP = os.environ.get("GUARDIAN_IP")


if not GUARDIAN_IP:
    logger.error("Guardian IP not configured in add-on options!")
else:
    if not GUARDIAN_IP.startswith("http"):
        GUARDIAN_IP = f"http://{GUARDIAN_IP}"
    # 3. Only create the client if we have an IP
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
    # 4. Check if the client exists before calling ping
    if not guardian:
        return JSONResponse(
            status_code=503, 
            content={"error": "Guardian client not initialized. Check add-on configuration."}
        )
    return await guardian.ping()


# ---------------------------
# Supervisor Test (HA only)
# ---------------------------
@app.get("/supervisor-test")
async def supervisor_test():
    # No more 'self.token' here! 
    # Just call a method on your supervisor object
    return await supervisor._get("/info")



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