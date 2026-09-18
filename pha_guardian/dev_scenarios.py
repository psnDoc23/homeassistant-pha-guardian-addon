"""Deterministic Home Assistant scenarios for the live development preview."""

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone


def _timestamp(hours_ago=0, minutes_ago=0):
    return (
        datetime.now(timezone.utc)
        - timedelta(hours=hours_ago, minutes=minutes_ago)
    ).replace(microsecond=0).isoformat()


def _dropout_cycles(entity_id, count):
    events = []
    for index in range(count):
        hours_ago = 40 - index * 2
        events.extend(
            [
                {
                    "entity_id": entity_id,
                    "state": "unavailable",
                    "when": _timestamp(hours_ago=hours_ago),
                },
                {
                    "entity_id": entity_id,
                    "state": "on",
                    "when": _timestamp(hours_ago=hours_ago - 1),
                },
            ]
        )
    return events


BASE_STATES = [
    {
        "entity_id": "light.kitchen_pendant",
        "state": "on",
        "attributes": {"friendly_name": "Kitchen Pendant"},
    },
    {
        "entity_id": "light.hallway",
        "state": "on",
        "attributes": {"friendly_name": "Hallway Lights"},
    },
    {
        "entity_id": "light.entry_lamp",
        "state": "on",
        "attributes": {"friendly_name": "Entry Lamp"},
    },
    {
        "entity_id": "switch.garage_fan",
        "state": "off",
        "attributes": {"friendly_name": "Garage Fan"},
    },
    {
        "entity_id": "sensor.utility_temperature",
        "state": "72",
        "attributes": {"friendly_name": "Utility Temperature"},
    },
    {
        "entity_id": "sensor.dave_phone_battery",
        "state": "unavailable",
        "attributes": {"friendly_name": "Phone Battery"},
    },
    {
        "entity_id": "automation.evening_hallway_lights",
        "state": "off",
        "attributes": {
            "id": "evening_hallway_lights",
            "friendly_name": "Evening Hallway Lights",
        },
    },
]

ENTITY_REGISTRY = [
    {
        "entity_id": "light.kitchen_pendant",
        "platform": "zha",
        "device_id": "device-kitchen",
    },
    {
        "entity_id": "light.hallway",
        "platform": "zha",
        "device_id": "device-hallway",
    },
    {
        "entity_id": "light.entry_lamp",
        "platform": "zha",
        "device_id": "device-entry",
    },
    {
        "entity_id": "switch.garage_fan",
        "platform": "zwave_js",
        "device_id": "device-garage",
    },
    {
        "entity_id": "sensor.utility_temperature",
        "platform": "zha",
        "device_id": "device-utility",
    },
    {
        "entity_id": "sensor.dave_phone_battery",
        "platform": "mobile_app",
        "device_id": "device-phone",
    },
]

DEVICE_REGISTRY = [
    {"id": "device-kitchen", "name": "Kitchen Pendant", "entry_type": None},
    {"id": "device-hallway", "name": "Hallway Lights", "entry_type": None},
    {"id": "device-entry", "name": "Entry Lamp", "entry_type": None},
    {"id": "device-garage", "name": "Garage Fan", "entry_type": None},
    {"id": "device-utility", "name": "Utility Sensor", "entry_type": None},
    {"id": "device-phone", "name": "Dave's Phone", "entry_type": "service"},
]

AUTOMATIONS = {
    "evening_hallway_lights": {
        "id": "evening_hallway_lights",
        "alias": "Evening Hallway Lights",
        "triggers": [{"trigger": "time", "at": "00:01:00"}],
        "actions": [{"action": "light.turn_off", "target": {"entity_id": "light.hallway"}}],
    }
}


def _scenario(label, description, histories=None, monitored=None, disk_free=90):
    return {
        "label": label,
        "description": description,
        "states": deepcopy(BASE_STATES),
        "entity_registry": deepcopy(ENTITY_REGISTRY),
        "device_registry": deepcopy(DEVICE_REGISTRY),
        "histories": histories or {},
        "logbooks": {},
        "automations": deepcopy(AUTOMATIONS),
        "monitored_automation_ids": monitored or [],
        "disk_free": disk_free,
        "disk_total": 100,
        "logs": "Home Assistant started normally.",
        "time_zone": "America/Denver",
    }


CORRELATED_TIME = _timestamp(hours_ago=2)

SCENARIOS = {
    "healthy": _scenario(
        "Healthy home",
        "No current diagnostic issues.",
    ),
    "device_dropout": _scenario(
        "Repeated device dropout",
        "One physical device repeatedly loses its connection.",
        histories={
            "light.kitchen_pendant": _dropout_cycles("light.kitchen_pendant", 6),
        },
    ),
    "correlated_dropout": _scenario(
        "Correlated dropout",
        "Three physical devices go offline together while a phone sensor is ignored.",
        histories={
            "light.entry_lamp": [
                {"entity_id": "light.entry_lamp", "state": "unavailable", "when": CORRELATED_TIME}
            ],
            "switch.garage_fan": [
                {"entity_id": "switch.garage_fan", "state": "unavailable", "when": CORRELATED_TIME}
            ],
            "sensor.utility_temperature": [
                {
                    "entity_id": "sensor.utility_temperature",
                    "state": "unavailable",
                    "when": CORRELATED_TIME,
                }
            ],
            "sensor.dave_phone_battery": [
                {
                    "entity_id": "sensor.dave_phone_battery",
                    "state": "unavailable",
                    "when": CORRELATED_TIME,
                }
            ],
        },
    ),
    "missed_automation": _scenario(
        "Missed automation",
        "A monitored automation appears not to have completed.",
        monitored=["evening_hallway_lights"],
    ),
}


class ScenarioStore:
    def __init__(self, initial="healthy"):
        self.current_name = initial if initial in SCENARIOS else "healthy"

    @property
    def current(self):
        return SCENARIOS[self.current_name]

    def select(self, name):
        if name not in SCENARIOS:
            raise KeyError(name)
        self.current_name = name
        return self.current

    def summaries(self):
        return [
            {
                "id": name,
                "label": data["label"],
                "description": data["description"],
            }
            for name, data in SCENARIOS.items()
        ]


class ScenarioSupervisor:
    """In-process implementation of the SupervisorClient contract for tests."""

    def __init__(self, scenario="healthy"):
        self.store = ScenarioStore(scenario)

    @property
    def data(self):
        return self.store.current

    async def _get(self, path):
        if path == "/host/info":
            return {
                "result": "ok",
                "data": {
                    "disk_free": self.data["disk_free"],
                    "disk_total": self.data["disk_total"],
                },
            }
        if path == "/core/info":
            return {"result": "ok", "data": {"healthy": True, "version": "2026.9.0"}}
        raise KeyError(path)

    async def _get_text(self, path):
        if path == "/core/logs":
            return {"logs": self.data["logs"]}
        raise KeyError(path)

    async def _get_core(self, path):
        if path == "/states":
            return deepcopy(self.data["states"])
        if path == "/config":
            return {"time_zone": self.data["time_zone"]}
        if path == "/config/entity_registry/list":
            return deepcopy(self.data["entity_registry"])
        if path == "/config/device_registry/list":
            return deepcopy(self.data["device_registry"])
        if path.startswith("/config/automation/config/"):
            automation_id = path.rsplit("/", 1)[-1]
            return deepcopy(self.data["automations"][automation_id])
        if path.startswith("/states/"):
            entity_id = path.split("/states/", 1)[1]
            return next(
                deepcopy(state)
                for state in self.data["states"]
                if state["entity_id"] == entity_id
            )
        raise KeyError(path)

    async def _post_core(self, path, body=None):
        return {"status": "ok"}

    async def _post_core_text(self, path, body=None):
        template = (body or {}).get("template", "")
        if "mobile + ios" in template:
            return json.dumps(
                [
                    entry["entity_id"]
                    for entry in self.data["entity_registry"]
                    if entry.get("platform") in {"mobile_app", "ios"}
                ]
            )
        if "device_id(eid)" in template:
            return json.dumps(
                {
                    entry["entity_id"]: entry.get("device_id") or ""
                    for entry in self.data["entity_registry"]
                }
            )
        if "device_attr(did" in template:
            return json.dumps(
                {
                    device["id"]: device.get("name_by_user")
                    or device.get("name")
                    or device["id"]
                    for device in self.data["device_registry"]
                }
            )
        if "integration_entities(intg)" in template:
            return json.dumps(
                {
                    entry["entity_id"]: entry.get("platform", "unknown")
                    for entry in self.data["entity_registry"]
                }
            )
        return json.dumps({})

    async def get_history(self, entity_id, hours=24):
        return deepcopy(self.data["histories"].get(entity_id, []))

    async def get_logbook(self, entity_id, hours=24):
        return deepcopy(self.data["logbooks"].get(entity_id, []))

    async def get_state(self, entity_id):
        return await self._get_core(f"/states/{entity_id}")

    async def get_entity_registry(self):
        return await self._get_core("/config/entity_registry/list")

    async def get_ha_config(self):
        return await self._get_core("/config")