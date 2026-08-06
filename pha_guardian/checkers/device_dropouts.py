# checkers/device_dropouts.py

from datetime import datetime, timezone

DROPOUT_THRESHOLD = 5 # minimum dropouts in window to raise an issue
HOURS_TO_CHECK = 48
PHYSICAL_DOMAINS = ["light", "switch", "binary_sensor", "sensor"]

# Integrations whose entities go unavailable for non-hardware reasons
# (phone leaves home, app disconnects, etc.) and must be excluded from
# dropout detection to avoid false alerts.
COMPANION_INTEGRATIONS = frozenset({
    "mobile_app",  # HA companion app (iOS and Android)
    "ios",         # legacy iOS integration
})


async def _get_device_info(supervisor) -> tuple:
    """
    Returns a 3-tuple:
      companion_ids   — frozenset of entity IDs belonging to companion-app
                        integrations (mobile_app, ios). These go unavailable
                        when a phone leaves home and must be skipped.
      entity_to_device — dict mapping entity_id -> device_id (str or None).
      device_names    — dict mapping device_id -> human-readable device name.

    All three fall back gracefully so the checker keeps working even when
    the entity registry or device registry endpoint is unavailable.
    """
    companion_ids = frozenset()
    entity_to_device = {}
    device_names = {}

    # --- Entity registry: companion filtering + entity→device mapping ---
    try:
        raw = await supervisor.get_entity_registry()
        if isinstance(raw, dict):
            registry = (
                raw.get("result")
                or raw.get("entity_registry")
                or raw.get("data")
                or []
            )
        elif isinstance(raw, list):
            registry = raw
        else:
            registry = []

        companion_ids = frozenset(
            entry["entity_id"]
            for entry in registry
            if isinstance(entry, dict)
            and entry.get("platform") in COMPANION_INTEGRATIONS
            and entry.get("entity_id")
        )
        entity_to_device = {
            entry["entity_id"]: entry.get("device_id")
            for entry in registry
            if isinstance(entry, dict) and entry.get("entity_id")
        }
    except Exception:
        pass

    # --- Device registry: device_id → friendly name ---
    try:
        raw_devices = await supervisor._get_core("/config/device_registry/list")
        if isinstance(raw_devices, dict):
            devices = (
                raw_devices.get("result")
                or raw_devices.get("devices")
                or raw_devices.get("data")
                or []
            )
        elif isinstance(raw_devices, list):
            devices = raw_devices
        else:
            devices = []

        service_device_ids = set()
        for dev in devices:
            if not isinstance(dev, dict):
                continue
            dev_id = dev.get("id")
            if not dev_id:
                continue
            # Prefer user-assigned name, then integration name, then model
            name = (
                dev.get("name_by_user")
                or dev.get("name")
                or dev.get("model")
                or dev_id
            )
            device_names[dev_id] = name
            # HA marks software/virtual devices (companion apps, weather services,
            # voice assistants, etc.) with entry_type="service". Physical hardware
            # devices have entry_type=None. Excluding by entry_type catches any
            # future virtual integration automatically without a code change.
            if dev.get("entry_type") == "service":
                service_device_ids.add(dev_id)

        # Extend companion_ids with entities whose device is service-type.
        # This is additive — the COMPANION_INTEGRATIONS frozenset above still
        # catches entities that have no device_id at all.
        service_entity_ids = frozenset(
            entity_id
            for entity_id, device_id in entity_to_device.items()
            if device_id in service_device_ids
        )
        companion_ids = companion_ids | service_entity_ids
    except Exception:
        pass

    return companion_ids, entity_to_device, device_names


async def check_device_dropouts(supervisor) -> list:
    """
    Scans all physical (non-group) entities for repeated unavailability in the
    last 48 hours.

    Entities that share the same HA device_id are grouped into a single issue
    so that a speaker with a "Loudness" and a "Crossfade" switch produces one
    card, not two.  Entities with no device_id (orphaned/template entities)
    fall back to per-entity issues.

    Companion-app entities (mobile_app / ios) are excluded before history is
    fetched: phone sensors going unavailable when someone leaves home are not
    hardware dropouts and must not surface as device issues.
    """
    issues = []

    try:
        states = await supervisor._get_core("/states")
    except Exception as e:
        return [{
            "id": "dropout_check_failed",
            "title": "Device dropout check failed",
            "severity": "unknown",
            "detail": str(e),
            "fixable": False,
        }]

    companion_ids, entity_to_device, device_names = await _get_device_info(supervisor)

    # Filter to physical entities only — skip groups, areas, and companion-app sensors
    candidates = [
        s for s in states
        if _is_physical_entity(s.get("entity_id", ""), companion_ids)
    ]

    # --- Per-entity dropout scan ---
    # Collect results keyed by entity_id before grouping.
    entity_results = {}   # entity_id -> {count, events, friendly_name}

    for state in candidates:
        entity_id = state.get("entity_id")
        try:
            entries = await supervisor.get_history(entity_id, hours=HOURS_TO_CHECK)

            # Filter to this entity only (logbook API filter is unreliable)
            entries = [e for e in entries if e.get("entity_id") == entity_id]

            events = _extract_dropout_events(entries)
            dropout_count = len(events)

            if dropout_count >= DROPOUT_THRESHOLD:
                entity_results[entity_id] = {
                    "count": dropout_count,
                    "events": events,
                    "friendly_name": state.get("attributes", {}).get(
                        "friendly_name", entity_id
                    ),
                }
        except Exception:
            # Skip entities we can't check — don't let one bad entity break the scan
            continue

    # --- Group by device_id ---
    # Entities sharing a device_id collapse into one issue.
    # Entities with no device_id are reported individually (orphan path).
    device_groups = {}   # device_id -> list of (entity_id, result_dict)
    orphans = []         # (entity_id, result_dict) with no device_id

    for entity_id, result in entity_results.items():
        device_id = entity_to_device.get(entity_id)
        if device_id:
            device_groups.setdefault(device_id, []).append((entity_id, result))
        else:
            orphans.append((entity_id, result))

    # --- Emit one issue per device group ---
    for device_id, members in device_groups.items():
        max_count = max(r["count"] for _, r in members)
        # Use the entity with the most dropouts as the representative for events
        primary_eid, primary_result = max(members, key=lambda m: m[1]["count"])
        entity_ids = [eid for eid, _ in members]
        device_name = device_names.get(device_id) or primary_result["friendly_name"]

        if len(members) == 1:
            detail = (
                f"{primary_eid} went unavailable {primary_result['count']} time(s) "
                f"in the last {HOURS_TO_CHECK} hours."
            )
        else:
            detail = (
                f"{len(members)} entities went unavailable up to {max_count} time(s) "
                f"in the last {HOURS_TO_CHECK} hours."
            )

        issues.append({
            "id": f"dropout_device_{device_id}",
            "title": f"Device dropout detected: {device_name}",
            "detail": detail,
            "severity": "high" if max_count >= 5 else "medium",
            "suggestion": (
                "This device is repeatedly losing connection. "
                "Check its power source, signal strength, and distance from the hub. "
                "If it's a Zigbee device, consider adding a repeater nearby."
            ),
            "fixable": False,
            "events": primary_result["events"],
            "entity_ids": entity_ids,
            "dropout_count": max_count,
        })

    # --- Emit per-entity issues for orphaned entities (no device_id) ---
    for entity_id, result in orphans:
        issues.append({
            "id": f"dropout_{entity_id}",
            "title": f"Device dropout detected: {result['friendly_name']}",
            "detail": (
                f"{entity_id} went unavailable {result['count']} time(s) "
                f"in the last {HOURS_TO_CHECK} hours."
            ),
            "severity": "high" if result["count"] >= 5 else "medium",
            "suggestion": (
                "This device is repeatedly losing connection. "
                "Check its power source, signal strength, and distance from the hub. "
                "If it's a Zigbee device, consider adding a repeater nearby."
            ),
            "fixable": False,
            "events": result["events"],
            "entity_ids": [entity_id],
            "dropout_count": result["count"],
        })

    return issues


def _is_physical_entity(entity_id: str, skip_ids: frozenset = frozenset()) -> bool:
    """
    Return True for physical device entities that should be monitored for
    dropouts.

    skip_ids: entity IDs to always exclude regardless of domain — used to
    filter out companion-app entities (mobile_app, ios) whose unavailability
    reflects a phone leaving home, not a hardware fault.
    """
    if entity_id in skip_ids:
        return False
    domain = entity_id.split(".")[0]
    if domain not in PHYSICAL_DOMAINS:
        return False
    # Skip known group/area patterns
    skip_keywords = ["group", "all", "area", "floor", "zone"]
    name_part = entity_id.split(".")[1] if "." in entity_id else ""
    return not any(kw in name_part.lower() for kw in skip_keywords)


def _count_dropouts(entries: list) -> int:
    """Counts how many times the entity transitioned to unavailable.
    Kept for backward compatibility; prefer _extract_dropout_events() for new code."""
    return sum(1 for e in entries if e.get("state") == "unavailable")


def _extract_dropout_events(entries: list) -> list:
    """
    Walks the chronological state-change history for a single entity and pairs
    each transition into 'unavailable' with the next recovery transition.

    Returns a list of dicts:
        {
            "started_at":       ISO-8601 string (when device went unavailable),
            "ended_at":         ISO-8601 string or None (when it recovered; None if still down),
            "duration_minutes": float or None (None if still down),
        }
    """
    events = []
    in_dropout = False
    dropout_start = None

    for entry in entries:
        state = entry.get("state")
        when_raw = entry.get("when")
        when = _parse_ts(when_raw)

        if state == "unavailable":
            if not in_dropout:
                # New dropout begins
                in_dropout = True
                dropout_start = when
        else:
            if in_dropout:
                # Device recovered
                in_dropout = False
                ended_at = when
                duration = None
                if dropout_start and ended_at:
                    diff = (ended_at - dropout_start).total_seconds()
                    duration = round(diff / 60, 2)
                events.append({
                    "started_at": dropout_start.isoformat() if dropout_start else None,
                    "ended_at": ended_at.isoformat() if ended_at else None,
                    "duration_minutes": duration,
                })
                dropout_start = None

    # Device is still unavailable at the end of the history window
    if in_dropout and dropout_start:
        events.append({
            "started_at": dropout_start.isoformat(),
            "ended_at": None,
            "duration_minutes": None,
        })

    return events


def _parse_ts(value: str):
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None
