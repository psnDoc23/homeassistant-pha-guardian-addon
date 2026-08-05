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


async def _get_companion_entity_ids(supervisor) -> frozenset:
    """
    Return entity IDs that belong to companion-app integrations (mobile_app,
    ios, etc.).  These sensors go unavailable when a phone leaves home — not
    because of a hardware or network fault — so they must be excluded from
    dropout detection.

    Falls back to an empty frozenset on any error so the checker keeps working
    even when the entity registry endpoint is unavailable.
    """
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
        return frozenset(
            entry["entity_id"]
            for entry in registry
            if isinstance(entry, dict)
            and entry.get("platform") in COMPANION_INTEGRATIONS
            and entry.get("entity_id")
        )
    except Exception:
        return frozenset()


async def check_device_dropouts(supervisor) -> list:
    """
    Scans all physical (non-group) entities for repeated unavailability
    in the last 48 hours. Returns one issue per affected device, including
    a timestamped events array for each dropout interval.

    Companion-app entities (mobile_app / ios) are excluded before history is
    fetched: phone sensors going unavailable when someone leaves home are not
    hardware dropouts and must not surface as individual device issues.
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

    # Fetch companion-app entity IDs to exclude from dropout detection.
    companion_ids = await _get_companion_entity_ids(supervisor)

    # Filter to physical entities only — skip groups, areas, and companion-app sensors
    candidates = [
        s for s in states
        if _is_physical_entity(s.get("entity_id", ""), companion_ids)
    ]

    for state in candidates:
        entity_id = state.get("entity_id")
        try:
            entries = await supervisor.get_history(entity_id, hours=HOURS_TO_CHECK)

            # Filter to this entity only (logbook API filter is unreliable)
            entries = [e for e in entries if e.get("entity_id") == entity_id]

            events = _extract_dropout_events(entries)
            dropout_count = len(events)

            if dropout_count >= DROPOUT_THRESHOLD:
                friendly_name = state.get("attributes", {}).get("friendly_name", entity_id)
                issues.append({
                    "id": f"dropout_{entity_id}",
                    "title": f"Device dropout detected: {friendly_name}",
                    "detail": (
                        f"{entity_id} went unavailable {dropout_count} time(s) "
                        f"in the last {HOURS_TO_CHECK} hours."
                    ),
                    "severity": "high" if dropout_count >= 5 else "medium",
                    "suggestion": (
                        "This device is repeatedly losing connection. "
                        "Check its power source, signal strength, and distance from the hub. "
                        "If it's a Zigbee device, consider adding a repeater nearby."
                    ),
                    "fixable": False,
                    "events": events,
                })
        except Exception:
            # Skip entities we can't check — don't let one bad entity break the whole scan
            continue

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
