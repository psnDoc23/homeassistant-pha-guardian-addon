# checkers/device_classifier.py
"""
Classifies every entity in the HA entity registry by:
  - integration  : the HA domain that owns the entity (e.g. "smartthings", "zha")
  - iot_class    : mirrors HA's manifest field (e.g. "cloud_push", "local_push")
  - protocol     : physical transport (zigbee / zwave / wifi / bluetooth / mixed / virtual / unknown)
  - is_local     : True when iot_class is local_push / local_polling / calculated

Strategy
--------
1. Fetch the full entity registry from HA (one API call).
2. For each unique integration domain, look up in KNOWN_INTEGRATIONS first.
3. If not found, fetch the integration manifest from HA to get iot_class dynamically.
4. Return a dict keyed by entity_id so callers can look up any entity cheaply.

The result is included in every push payload as "device_registry" so the Django
side can upsert DeviceClassification rows and gate paid features on is_local.
"""

from logging import getLogger

logger = getLogger(__name__)

# ---------------------------------------------------------------------------
# Static lookup table for common integrations.
# iot_class mirrors the field name used in HA integration manifests.
# ---------------------------------------------------------------------------
KNOWN_INTEGRATIONS = {
    # Local Zigbee
    "zha":              {"iot_class": "local_push",    "protocol": "zigbee"},
    "mqtt":             {"iot_class": "local_push",    "protocol": "zigbee"},  # Zigbee2MQTT

    # Local Z-Wave
    "zwave_js":         {"iot_class": "local_push",    "protocol": "zwave"},

    # Local Wi-Fi / hub-based (hub talks locally to HA)
    "esphome":          {"iot_class": "local_push",    "protocol": "wifi"},
    "shelly":           {"iot_class": "local_push",    "protocol": "wifi"},
    "sonos":            {"iot_class": "local_push",    "protocol": "wifi"},
    "lutron_caseta":    {"iot_class": "local_push",    "protocol": "wifi"},
    "hue":              {"iot_class": "local_push",    "protocol": "zigbee"},  # bridge is LAN
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

    # Virtual / internal (no physical device)
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


async def classify_devices(supervisor) -> dict:
    """
    Query HA's entity registry and return a classification dict:

        {
            "binary_sensor.basement_sensor_motion": {
                "integration": "smartthings",
                "iot_class":   "cloud_push",
                "protocol":    "mixed",
                "is_local":    False,
            },
            ...
        }

    Unknown integrations are resolved by fetching their HA manifest.
    Manifest fetch failures fall back to iot_class="unknown".
    """
    try:
        registry = await supervisor.get_entity_registry()
    except Exception as e:
        logger.warning(f"Could not fetch entity registry: {e}")
        return {}

    # Cache manifest lookups so we only fetch each domain once per run
    manifest_cache: dict[str, dict] = {}

    result = {}
    for entry in registry:
        entity_id = entry.get("entity_id")
        platform = entry.get("platform")  # integration domain

        if not entity_id or not platform:
            continue

        # 1. Known integration — use static table
        if platform in KNOWN_INTEGRATIONS:
            profile = KNOWN_INTEGRATIONS[platform]
            result[entity_id] = {
                "integration": platform,
                "iot_class":   profile["iot_class"],
                "protocol":    profile["protocol"],
                "is_local":    _is_local(profile["iot_class"]),
            }
            continue

        # 2. Unknown integration — try fetching the manifest
        if platform not in manifest_cache:
            try:
                manifest = await supervisor.get_manifest(platform)
                manifest_cache[platform] = manifest
            except Exception:
                manifest_cache[platform] = {}

        manifest = manifest_cache.get(platform, {})
        iot_class = manifest.get("iot_class", "unknown")

        result[entity_id] = {
            "integration": platform,
            "iot_class":   iot_class,
            "protocol":    "unknown",
            "is_local":    _is_local(iot_class),
        }

    local_count = sum(1 for v in result.values() if v["is_local"])
    cloud_count = sum(1 for v in result.values() if not v["is_local"] and v["iot_class"] != "unknown")
    logger.info(
        f"Device classifier: {len(result)} entities — "
        f"{local_count} local, {cloud_count} cloud"
    )
    return result
