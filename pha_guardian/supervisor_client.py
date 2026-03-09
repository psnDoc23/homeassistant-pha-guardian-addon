import os
import httpx
from logging import getLogger
import re


logger = getLogger(__name__)

class SupervisorClient:
    def __init__(self):
        self.base_url = "http://supervisor"
        # Don't fail here, just prepare
        self.client = httpx.AsyncClient(timeout=10.0)

    @property
    def token(self):
        t = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
        logger.debug(f"SUPERVISOR_TOKEN present: {bool(os.environ.get('SUPERVISOR_TOKEN'))}")
        logger.debug(f"HASSIO_TOKEN present: {bool(os.environ.get('HASSIO_TOKEN'))}")


        if not t:
            logger.error("SUPERVISOR_TOKEN is missing from environment!")
        return t

    async def _get(self, path: str):
        token = self.token
        if not token:
            raise Exception("Authentication token missing")
        
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        
        response = await self.client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()



    async def _get_text(self, path: str):
        token = self.token
        if not token:
            raise Exception("Authentication token missing")
        
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        
        response = await self.client.get(url, headers=headers)
        response.raise_for_status()
        clean = re.sub(r'\x1b\[[0-9;]*m', '', response.text)
        return {"logs": clean}


    async def get_state(self, entity_id: str):
        return await self._get_core(f"/states/{entity_id}")


    async def _get_core(self, path: str):
        token = self.token
        if not token:
            raise Exception("Authentication token missing")
        
        url = f"{self.base_url}/core/api{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "X-HA-Access": token,
        }
        
        response = await self.client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


    async def get_logbook(self, entity_id: str):
        return await self._get_core(f"/logbook?entity={entity_id}")

