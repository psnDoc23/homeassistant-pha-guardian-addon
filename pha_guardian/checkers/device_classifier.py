# checkers/device_classifier.py
"""
Classifies every entity in the HA entity registry by:
  - integration  : the HA domain that owns the entity (e.g. "smartthings", "zha")
  - iot_class    : mirrors HA's manifest field (e.g. "cloud_push", "local_push")
  - protocol     : physical transport (zigbee / zwave / wifi / bluetooth / mixed / virtual / unknown)
  - is_local     : True when iot_class is local_push / local_polling / calculated

Strategy
--------
1. Try GET /api/config/entity_registry/list (REST endpoint, not available on all HA versions).
2. If that fails or returns 0 entries, fall back to POST /api/template using
   HA's integration_entities() Jinja2 function (available since HA 2022.4).
   This is reliable and proven to work via the supervisor token.
3. Build a classification dict keyed by entity_id.

The result is included in every push payload as "device_registry".
"""

from logging import getLogger

logger = getLogger(__name__)

# ---------------------------------------------------------------------------
# Static lookup table for common integrations.
# ---------------------------------------------------------------------------
KNOWN_INTEGRATIONS = {
    # Local Zigbee
    "zha":              {"iot_class": "local_push",    "protocol": "zigbee"},
    "mqtt":             {"iot_class": "local_push",    "protocol": "zigbee"},  # Zigbee2MQTT

    # Local Z-Wave
    "zwave_js":         {"iot_class": "local_push",    "protocol": "zwave"},

    # Local Wi-Fi / hub-based
    "esphome":          {"iot_class": "local_push",    "protocol": "wifi"},
    "shelly":           {"iot_class": "local_push",    "protocol": "wifi"},
    "sonos":            {"iot_class": "local_push",    "protocol": "wifi"},
    "lutron_caseta":    {"iot_class": "local_push",    "protocol": "wifi"},
    "hue":              {"iot_class": "local_push",    "protocol": "zigbee"},
    "wled":             {"iot_class": "local_push",    "protocol": "wifi"},
    "kasa":             {"iot_class": "local_polling", "protocol": "wifi"},
    "homekit":          {"iot_class": "local_push",    "protocol": "wifi"},
    "matter":           {"iot_class": "local_push",    "protocol": "wifi"},

    # Local Bluetooth
    "bluetooth":        {"iot_class": "local_push",    "protocol": "bluetooth"},
    "bluetooth_le":     {"iot_class": "local_push",    "protocol": "bluetooth"},

    # Cloud
    "smartthings":      {"iot_class": "cloud_push",    "protocol": "mixed"},
    "tuya":             {"iot_class": "cloud_push",    "protocol": "wifi"},
    "google":           {"iot_class": "cloud_push",    "protocol": "wifi"},
    "google_assistant": {"iot_class": "cloud_push",    "protocol": "wifi"},
    "nest":             {"iot_class": "cloud_polling", "protocol": "wifi"},
    "ring":             {"iot_class": "cloud_push",    "protocol": "wifi"},
    "august":           {"iot_class": "cloud_push",    "protocol": "wifi"},
    "govee":            {"iot_class": "cloud_push",    "protocol": "wifi"},
    "ecobee":           {"iot_class": "cloud_push",    "protocol": "wifi"},
    "rainbird":         {"iot_class": "cloud_push",    "protocol": "wifi"},
    "lifx":             {"iot_class": "cloud_push",    "protocol": "wifi"},
    "wyze":             {"iot_class": "cloud_push",    "protocol": "wifi"},
    "alexa":            {"iot_class": "cloud_push",    "protocol": "wifi"},
    "tado":             {"iot_class": "cloud_polling", "protocol": "wifi"},
    "vera":             {"iot_class": "cloud_polling", "protocol": "mixed"},

    # Virtual / internal
    "template":         {"iot_class": "calculated",    "protocol": "virtual"},
    "group":            {"iot_class": "calculated",    "protocol": "virtual"},
    "input_boolean":    {"iot_class": "calculated",    "protocol": "virtual"},
    "input_number":     {"iot_class": "calculated",    "protocol": "virtual"},
    "input_select":     {"iot_class": "calculated",    "protocol": "virtual"},
    "input_text":       {"iot_class": "calculated",    "protocol": "virtual"},
    "input_datetime":   {"iot_class": "calculated",    "protocol": "virtual"},
    "script":           {"iot_class": "calculated",    "protocol": "virtual"},
    "automation":       {"iot_class": "calculated",    "protocol": "virtual"},
    "sun":              {"iot_class": "calculated",    "protocol": "virtual"},
    "zone":             {"iot_class": "calculated",    "protocol": "virtual"},
    "counter":          {"iot_class": "calculated",    "protocol": "virtual"},
    "timer":            {"iot_class": "calculated",    "protocol": "virtual"},
    "person":           {"iot_class": "calculated",    "protocol": "virtual"},
    "schedule":         {"iot_class": "calculated",    "protocol": "virtual"},
}

LOCAL_IOT_CLASSES = {"local_push", "local_polling", "calculated"}


def _is_local(iot_class: str) -> bool:
    return iot_class in LOCAL_IOT_CLASSES


def _build_result_from_platform_map(platform_map: dict) -> dict:
    """
    Given a dict of {entity_id: integration_name}, return the full
    classification dict using KNOWN_INTEGRATIONS.
    Entities whose integration isn't in KNOWN_INTEGRATIONS get iot_class='unknown'.
    """
    result = {}
    for entity_id, platform in platform_map.items():
        if platform in KNOWN_INTEGRATIONS:
            profile = KNOWN_INTEGRATIONS[platform]
            result[entity_id] = {
                "integration": platform,
                "iot_class":   profile["iot_class"],
                "protocol":    profile["protocol"],
                "is_local":    _is_local(profile["iot_class"]),
            }
        else:
            result[entity_id] = {
                "integration": platform,
                "iot_class":   "unknown",
                "protocol":    "unknown",
                "is_local":    False,
            }
    return result


async def classify_devices(supervisor) -> dict:
    """
    Return a classification dict keyed by entity_id.

    Tries the entity registry REST endpoint first; falls back to the
    template engine approach if that endpoint is unavailable.
    """

    # ------------------------------------------------------------------
    # Path 1: entity registry REST endpoint
    # ------------------------------------------------------------------
    try:
        raw = await supervisor.get_entity_registry()

        # HA sometimes wraps the response in a dict
        if isinstance(raw, dict):
            registry = (
                raw.get("result")
                or raw.get("entity_registry")
                or raw.get("data")
                or []
            )
            logger.info(f"Entity registry (REST) returned a dict — keys: {list(raw.keys())}, "
                        f"extracted {len(registry)} entries")
        elif isinstance(raw, list):
            registry = raw
        else:
            logger.warning(f"Entity registry (REST) returned unexpected type {type(raw)}, "
                           f"will use template fallback")
            registry = []

        if registry:
            logger.info(f"Entity registry (REST): {len(registry)} entries")
            platform_map = {
                entry.get("entity_id"): entry.get("platform")
                for entry in registry
                if entry.get("entity_id") and entry.get("platform")
            }
            result = _build_result_from_platform_map(platform_map)
            _log_summary(result, source="REST")
            return result
        else:
            logger.info("Entity registry (REST) returned 0 entries — trying template fallback")

    except Exception as e:
        logger.warning(f"Entity registry (REST) failed ({type(e).__name__}: {e}) "
                       f"— trying template fallback")

    # ------------------------------------------------------------------
    # Path 2: template engine fallback (integration_entities, HA 2022.4+)
    # ------------------------------------------------------------------
    try:
        known_integrations = list(KNOWN_INTEGRATIONS.keys())
        platform_map = await supervisor.get_entity_platforms_via_template(known_integrations)
        logger.info(f"Entity registry (template): {len(platform_map)} entities matched "
                    f"across {len(known_integrations)} known integrations")
        result = _build_result_from_platform_map(platform_map)
        _log_summary(result, source="template")
        return result

    except Exception as e:
        logger.error(f"Entity registry (template) also failed: {type(e).__name__}: {e}")
        return {}


def _log_summary(result: dict, source: str):
    local_count = sum(1 for v in result.values() if v["is_local"])
    cloud_count = sum(1 for v in result.values() if not v["is_local"] and v["iot_class"] != "unknown")
    logger.info(
        f"Device classifier ({source}): {len(result)} entities — "
        f"{local_count} local, {cloud_count} cloud"
    )
