# checkers/correlated_dropouts.py

from datetime import datetime

HOURS_TO_CHECK = 48
WINDOW_MINUTES = 3          # devices dropping within this window are "correlated"
MIN_DEVICES_IN_WINDOW = 3   # need at least this many to flag a correlated event
PHYSICAL_DOMAINS = ["light", "switch", "binary_sensor", "sensor"]


async def check_correlated_dropouts(supervisor) -> list:
    """
    Detects when multiple distinct devices went unavailable within the same
    short time window — a signal of a network or hub event rather than a
    single flaky device. Returns at most a few summary issues.
    """
    try:
        states = await supervisor._get_core("/states")
    except Exception as e:
        return [{
            "id": "correlated_check_failed",
            "title": "Correlated dropout check failed",
            "severity": "unknown",
            "detail": str(e),
            "fixable": False,
        }]

    candidates = [
        s for s in states
        if _is_physical_entity(s.get("entity_id", ""))
    ]

    # Collect every (timestamp, entity_id) where a device went unavailable
    dropout_events = []
    for state in candidates:
        entity_id = state.get("entity_id")
        try:
            entries = await supervisor.get_history(entity_id, hours=HOURS_TO_CHECK)
            for e in entries:
                if e.get("state") == "unavailable" and e.get("when"):
                    ts = _parse(e["when"])
                    if ts:
                        dropout_events.append((ts, entity_id))
        except Exception:
            continue

    # Sort chronologically so we can sweep a window across them
    dropout_events.sort(key=lambda x: x[0])

    issues = []
    used_indices = set()

    for i in range(len(dropout_events)):
        if i in used_indices:
            continue
        window_start = dropout_events[i][0]
        # Gather all events within WINDOW_MINUTES of this one
        group = []
        for j in range(i, len(dropout_events)):
            if (dropout_events[j][0] - window_start).total_seconds() <= WINDOW_MINUTES * 60:
                group.append(j)
            else:
                break

        distinct_entities = {dropout_events[k][1] for k in group}
        if len(distinct_entities) >= MIN_DEVICES_IN_WINDOW:
            for k in group:
                used_indices.add(k)
            issues.append({
                "id": f"correlated_dropout_{int(window_start.timestamp())}",
                "title": f"Correlated dropout: {len(distinct_entities)} devices offline together",
                "detail": (
                    f"{len(distinct_entities)} devices went unavailable within "
                    f"{WINDOW_MINUTES} minutes around {window_start.strftime('%Y-%m-%d %H:%M UTC')}. "
                    "This often indicates a network or hub event rather than a single faulty device."
                ),
                "severity": "high",
                "suggestion": (
                    "Multiple devices dropping at once usually points to your network "
                    "or Home Assistant hub, not the individual devices. Check whether your "
                    "router, Wi-Fi, or Zigbee/Z-Wave coordinator restarted or lost power "
                    "around this time."
                ),
                "fixable": False,
                "involved_entities": sorted(distinct_entities), 
            })

    return issues


def _is_physical_entity(entity_id: str) -> bool:
    domain = entity_id.split(".")[0]
    if domain not in PHYSICAL_DOMAINS:
        return False
    skip_keywords = ["group", "all", "area", "floor", "zone"]
    name_part = entity_id.split(".")[1] if "." in entity_id else ""
    return not any(kw in name_part.lower() for kw in skip_keywords)


def _parse(when: str):
    try:
        return datetime.fromisoformat(when)
    except Exception:
        return None



        