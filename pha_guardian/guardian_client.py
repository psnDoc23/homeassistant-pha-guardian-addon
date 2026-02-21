import httpx
import logging

logger = logging.getLogger(__name__)

class GuardianClient:
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

    async def _get(self, path: str):
        url = f"{self.base_url}{path}"
        logger.info({"event": "guardian_request", "method": "GET", "url": url})

        try:
            response = await self.client.get(url)
            response.raise_for_status()

            logger.info({
                "event": "guardian_response",
                "status_code": response.status_code,
                "url": url
            })

            return response.json()

        except Exception as e:
            logger.error({
                "event": "guardian_error",
                "url": url,
                "error": str(e)
            })
            raise

    async def ping(self):
        return await self._get("/status")
    
    