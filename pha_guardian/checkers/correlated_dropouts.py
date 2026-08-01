# checkers/correlated_dropouts.py

from datetime import datetime, timezone, timedelta
import hashlib

HOURS_TO_CHECK = 48
WINDOW_MINUTES = 3          # devices dropping within this window are "correlated"
MIN_DEVICES_IN_WINDOW = 3   # need at least this many to flag a correlated event
PHYSICAL_DOMAINS = ["light", "switch", "binary_sensor", "sensor"]


def _format_local(dt: datetime, ha_timezone: str | None) -> str:
    """Format a datetime in the HA local timezone, falling back to UTC."""
    try:
        if ha_timezone:
            try:
                from zoneinfo import ZoneInfo
            except ImportError:
                from backports.zoneinfo import ZoneInfo
            local_dt = dt.astimezone(ZoneInfo(ha_timezone))
            abbr = local_dt.strftime('%Z')
            return local_dt.strftime(f'%Y-%m-%d %H:%M {abbr}')
    except Exception:
        pass
    # Fallback: display UTC explicitly
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime('%Y-%m-%d %H:%M UTC')


async def check_correlated_dropouts(supervisor, ha_timezone: str | None = None) -> list:
    """
    Detects when multiple distinct devices went unavailable within the same
    short time window — a signal of a network or hub event rather than a
    single flaky device.

    Groups all windows that share the same device set into a single issue so
    the dashboard shows one entry per device group rather than one per event.
    The issue ID is derived from the sorted device set, making it stable across
    pushes so user dismissals persist correctly.

    ha_timezone: IANA timezone name from HA's config (e.g. "America/Denver").
    Timestamps in the detail string are shown in this timezone when provided.
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

    # Identify each correlated window
    windows = []  # list of (window_start, frozenset of entity_ids)
    used_indices = set()

    for i in range(len(dropout_events)):
        if i in used_indices:
            continue
        window_start = dropout_events[i][0]
        group = []
        for j in range(i, len(dropout_events)):
            if (dropout_events[j][0] - window_start).total_seconds() <= WINDOW_MINUTES * 60:
                group.append(j)
            else:
                break

        distinct_entities = frozenset(dropout_events[k][1] for k in group)
        if len(distinct_entities) >= MIN_DEVICES_IN_WINDOW:
            for k in group:
                used_indices.add(k)
            windows.append((window_start, distinct_entities))

    # Group windows by device set — same devices = same underlying issue
    # Key: frozenset of entity_ids → list of window_start timestamps
    groups: dict[frozenset, list] = {}
    for window_start, entities in windows:
        groups.setdefault(entities, []).append(window_start)

    issues = []
    for entities, timestamps in groups.items():
        sorted_entities = sorted(entities)
        # Stable ID: hash of the sorted device set, not a timestamp
        device_key = "|".join(sorted_entities)
        stable_hash = hashlib.sha1(device_key.encode()).hexdigest()[:10]
        issue_id = f"correlated_dropout_{stable_hash}"

        count = len(timestamps)
        most_recent = max(timestamps)
        first_seen = min(timestamps)

        most_recent_fmt = _format_local(most_recent, ha_timezone)
        if count == 1:
            time_detail = f"around {most_recent_fmt}"
        else:
            time_detail = (
                f"{count} times in the last {HOURS_TO_CHECK}h "
                f"(most recently {most_recent_fmt})"
            )

        issues.append({
            "id": issue_id,
            "title": f"Correlated dropout: {len(sorted_entities)} devices offline together",
            "detail": (
                f"{len(sorted_entities)} devices went unavailable within "
                f"{WINDOW_MINUTES} minutes of each other {time_detail}. "
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
            "involved_entities": sorted_entities,
            "occurrence_count": count,
            "most_recent_ts": most_recent.isoformat(),
            "first_seen_ts": first_seen.isoformat(),
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



        
