import asyncio
import json
from unittest.mock import AsyncMock

from .ha_metadata import get_entity_device_mapping


def test_registry_fills_partial_template_device_mapping():
    supervisor = AsyncMock()
    supervisor._post_core_text = AsyncMock(
        side_effect=[
            json.dumps({"light.kitchen": ""}),
            json.dumps({"device-kitchen": "device-kitchen"}),
        ]
    )
    supervisor.get_entity_registry = AsyncMock(
        return_value=[
            {
                "entity_id": "light.kitchen",
                "platform": "zha",
                "device_id": "device-kitchen",
            }
        ]
    )
    supervisor._get_core = AsyncMock(
        return_value=[
            {"id": "device-kitchen", "name": "Kitchen Pendant", "entry_type": None}
        ]
    )

    entity_map, names = asyncio.run(
        get_entity_device_mapping(supervisor, ["light.kitchen"])
    )

    assert entity_map == {"light.kitchen": "device-kitchen"}
    assert names == {"device-kitchen": "Kitchen Pendant"}


def test_registry_replaces_device_id_used_as_template_name():
    supervisor = AsyncMock()
    supervisor._post_core_text = AsyncMock(
        side_effect=[
            json.dumps({"light.kitchen": "device-kitchen"}),
            json.dumps({"device-kitchen": "device-kitchen"}),
        ]
    )
    supervisor.get_entity_registry = AsyncMock(return_value=[])
    supervisor._get_core = AsyncMock(
        return_value=[
            {"id": "device-kitchen", "name": "Kitchen Pendant", "entry_type": None}
        ]
    )

    _, names = asyncio.run(
        get_entity_device_mapping(supervisor, ["light.kitchen"])
    )

    assert names == {"device-kitchen": "Kitchen Pendant"}