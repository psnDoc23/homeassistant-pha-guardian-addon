"""Shared Home Assistant metadata lookups used by dropout checkers."""

import json


COMPANION_INTEGRATIONS = frozenset({"mobile_app", "ios"})


async def get_excluded_entity_ids(supervisor) -> frozenset:
    """Return companion-app and service-backed entities that are not hardware."""
    excluded = set()

    try:
        template = (
            "{%- set mobile = integration_entities('mobile_app') | list -%}"
            "{%- set ios = integration_entities('ios') | list -%}"
            "{{ (mobile + ios) | tojson }}"
        )
        text = await supervisor._post_core_text("/template", {"template": template})
        ids = json.loads(text)
        if isinstance(ids, list):
            excluded.update(ids)
    except Exception:
        pass

    entities = []
    try:
        entities = await supervisor.get_entity_registry()
        excluded.update(
            entry.get("entity_id")
            for entry in entities
            if isinstance(entry, dict)
            and entry.get("entity_id")
            and entry.get("platform") in COMPANION_INTEGRATIONS
        )
    except Exception:
        pass

    try:
        devices = await supervisor._get_core("/config/device_registry/list")
        service_device_ids = {
            device.get("id")
            for device in devices
            if isinstance(device, dict) and device.get("entry_type") == "service"
        }
        excluded.update(
            entry.get("entity_id")
            for entry in entities
            if isinstance(entry, dict)
            and entry.get("entity_id")
            and entry.get("device_id") in service_device_ids
        )
    except Exception:
        pass

    return frozenset(excluded)


async def get_entity_device_mapping(supervisor, entity_ids: list) -> tuple[dict, dict]:
    """Resolve entity IDs to device IDs and human-readable device names."""
    entity_to_device = {}
    device_names = {}

    if not entity_ids:
        return entity_to_device, device_names

    try:
        entities_json = json.dumps(entity_ids)
        template = (
            "{%- set entities = " + entities_json + " -%}"
            "{%- set ns = namespace(result={}) -%}"
            "{%- for eid in entities -%}"
            "  {%- set did = device_id(eid) -%}"
            "  {%- set ns.result = dict(ns.result, **{eid: did | default('', true)}) -%}"
            "{%- endfor -%}"
            "{{ ns.result | tojson }}"
        )
        text = await supervisor._post_core_text("/template", {"template": template})
        raw_map = json.loads(text)
        if isinstance(raw_map, dict):
            entity_to_device.update({key: value or None for key, value in raw_map.items()})
    except Exception:
        pass

    try:
        entities = await supervisor.get_entity_registry()
        for entry in entities:
            entity_id = entry.get("entity_id") if isinstance(entry, dict) else None
            if entity_id in entity_ids and not entity_to_device.get(entity_id):
                entity_to_device[entity_id] = entry.get("device_id")
    except Exception:
        pass

    device_ids = {device_id for device_id in entity_to_device.values() if device_id}
    if not device_ids:
        return entity_to_device, device_names

    try:
        device_ids_json = json.dumps(sorted(device_ids))
        template = (
            "{%- set device_ids = " + device_ids_json + " -%}"
            "{%- set ns = namespace(result={}) -%}"
            "{%- for did in device_ids -%}"
            "  {%- set name = device_attr(did, 'name_by_user') or device_attr(did, 'name') or did -%}"
            "  {%- set ns.result = dict(ns.result, **{did: name | string}) -%}"
            "{%- endfor -%}"
            "{{ ns.result | tojson }}"
        )
        text = await supervisor._post_core_text("/template", {"template": template})
        raw_names = json.loads(text)
        if isinstance(raw_names, dict):
            device_names.update(raw_names)
    except Exception:
        pass

    try:
        devices = await supervisor._get_core("/config/device_registry/list")
        for device in devices:
            if not isinstance(device, dict) or device.get("id") not in device_ids:
                continue
            current_name = device_names.get(device["id"])
            if not current_name or current_name == device["id"]:
                device_names[device["id"]] = (
                    device.get("name_by_user") or device.get("name") or device["id"]
                )
    except Exception:
        pass

    return entity_to_device, device_names