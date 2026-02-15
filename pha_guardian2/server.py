# server.py
import asyncio
import logging
from fastapi import FastAPI
import uvicorn

from guardian.checks.availability import check_api_availability

logging.basicConfig(level=logging.INFO, format='[Guardian] %(message)s')

# TODO: Replace this with your actual Guardian API endpoint
GUARDIAN_API_URL = "http://your_guardian_ip_or_cloud_endpoint"

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def status():
    return {"status": "running", "check": "api_availability"}


async def periodic_checks():
    """Run API availability checks every 60 seconds."""
    logging.info("Starting periodic Guardian checks")

    while True:
        logging.info("Running API availability check...")
        result = check_api_availability(GUARDIAN_API_URL)
        logging.info(f"API check result: {result}")

        await asyncio.sleep(60)


@app.on_event("startup")
async def startup_event():
    """Start background tasks when the server starts."""
    asyncio.create_task(periodic_checks())
    logging.info("Guardian server startup complete")


def run():
    logging.info("Starting Guardian FastAPI server on port 8099")
    uvicorn.run(app, host="0.0.0.0", port=8099)


if __name__ == "__main__":
    run()
