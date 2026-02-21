import os
import httpx
from logging import getLogger

logger = getLogger(__name__)

class SupervisorClient:
    def __init__(self):
        self.base_url = "http://supervisor"
        self.token = os.environ.get("SUPERVISOR_TOKEN")

        if not self.token:
            logger.warning("SUPERVISOR_TOKEN not found — running in local dev mode")

        self.client = httpx.AsyncClient(
            timeout=10.0,
            headers={"Authorization": f"Bearer {self.token}"} if self.token else {}
        )

    async def _get(self, path: str):
        if not self.token:
            # Local dev mode — return stub
            return {
                "error": "Supervisor API not available in local dev environment",
                "path": path
            }

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

