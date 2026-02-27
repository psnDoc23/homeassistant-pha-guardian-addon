import os
import httpx
from logging import getLogger

logger = getLogger(__name__)

class SupervisorClient:
    def __init__(self):
        dev_mode = os.environ.get("DEV_MODE") == "1"

        if dev_mode:
            self.base_url = "http://localhost:8099"
            self.token = None
            logger.warning("Running in DEV_MODE — using mock Supervisor at localhost:8099")
        else:
            self.base_url = "http://supervisor"
            self.token = os.environ.get("SUPERVISOR_TOKEN")
            if not self.token:
                logger.warning("SUPERVISOR_TOKEN not found — Supervisor calls will fail")

        self.client = httpx.AsyncClient(
            timeout=10.0,
            headers={"Authorization": f"Bearer {self.token}"} if self.token else {}
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

