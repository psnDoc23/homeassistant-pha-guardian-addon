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
        # Fetch it fresh every time or cache it
        t = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
        print(os.environ.get("SUPERVISOR_TOKEN"))
        print(os.environ.get("HASSIO_TOKEN"))
        print(t) 

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

    
