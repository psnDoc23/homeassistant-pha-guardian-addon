import os
import httpx
from logging import getLogger

logger = getLogger(__name__)

class SupervisorClient:
    def __init__(self):
        # 1. DEBUG: This will show us every variable HA is actually passing in
        logger.info(f"DEBUG: Available Env Vars: {list(os.environ.keys())}")

        dev_mode = os.environ.get("DEV_MODE") == "1"

        if dev_mode:
            self.base_url = "http://localhost:8099"
            self.token = None
            logger.warning("Running in DEV_MODE — using mock Supervisor at localhost:8099")
        else:
            self.base_url = "http://supervisor"
            
            # 2. Try both modern and legacy token names
            self.token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
            
            if not self.token:
                logger.warning("SUPERVISOR_TOKEN not found — Supervisor calls will fail")

        # 3. Ensure headers are only set if we actually have a token
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        self.client = httpx.AsyncClient(
            timeout=10.0,
            headers=headers
        )

    async def _get(self, path: str):
        url = f"{self.base_url}{path}"
        logger.info({"event": "supervisor_request", "url": url})

        try:
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()

        except Exception as e:
            logger.error({"event": "supervisor_error", "url": url, "error": str(e)})
            raise

    async def get_logs(self):
        return await self._get("/logs")
        

        