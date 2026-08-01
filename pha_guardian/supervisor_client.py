import json
import os
import httpx
from logging import getLogger
import re


logger = getLogger(__name__)

class SupervisorClient:
    def __init__(self):
        self.base_url = "http://supervisor"
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

    async def _post_core(self, path: str, body: dict = {}):
        token = self.token
        if not token:
            raise Exception("Authentication token missing")
        url = f"{self.base_url}/core/api{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        response = await self.client.post(url, headers=headers, json=body)
        response.raise_for_status()
        return response.json()

    async def _post_core_text(self, path: str, body: dict = {}) -> str:
        """POST to HA core API and return raw text (for the /template endpoint)."""
        token = self.token
        if not token:
            raise Exception("Authentication token missing")
        url = f"{self.base_url}/core/api{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        response = await self.client.post(url, headers=headers, json=body)
        response.raise_for_status()
        return response.text

    async def get_logbook(self, entity_id: str, hours: int = 24):
        from datetime import datetime, timezone, timedelta
        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        data = await self._get_core(f"/logbook/{start}?entity_id={entity_id}&minimal_response=false")
        return [entry for entry in data if entry.get("entity_id") == entity_id]

    async def get_history(self, entity_id: str, hours: int = 24):
        from datetime import datetime, timezone, timedelta
        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        data = await self._get_core(
            f"/history/period/{start}?filter_entity_id={entity_id}&no_attributes=true"
        )
        if not data or not isinstance(data, list):
            return []
        entries = []
        for entity_history in data:
            for entry in entity_history:
                if entry.get("entity_id") == entity_id:
                    entries.append({
                        "entity_id": entry.get("entity_id"),
                        "state": entry.get("state"),
                        "when": entry.get("last_changed"),
                    })
        return entries

    async def get_ha_config(self) -> dict:
        """Return HA's global config (includes time_zone, latitude, unit_system, etc.)."""
        return await self._get_core("/config")

    async def get_entity_registry(self) -> list:
        """Return all entity registry entries from HA core (REST endpoint)."""
        return await self._get_core("/config/entity_registry/list")

    async def get_manifest(self, domain: str) -> dict:
        """Return the integration manifest for a given domain."""
        return await self._get_core(f"/manifests/{domain}")

    async def get_entity_platforms_via_template(self, integrations: list) -> dict:
        """
        Use HA's Jinja2 template engine to map entity_id -> integration name.

        Calls integration_entities() for each known integration (available in
        HA 2022.4+). This is the reliable fallback when the entity registry
        REST endpoint is not accessible.

        Returns: {"binary_sensor.motion_hall": "zha", "light.kitchen": "hue", ...}
        """
        integrations_json = json.dumps(integrations)
        template = (
            "{%- set integrations = " + integrations_json + " -%}"
            "{%- set ns = namespace(result={}) -%}"
            "{%- for intg in integrations -%}"
            "  {%- for eid in integration_entities(intg) -%}"
            "    {%- set ns.result = dict(ns.result, **{eid: intg}) -%}"
            "  {%- endfor -%}"
            "{%- endfor -%}"
            "{{ ns.result | tojson }}"
        )
        text = await self._post_core_text("/template", {"template": template})
        return json.loads(text)
