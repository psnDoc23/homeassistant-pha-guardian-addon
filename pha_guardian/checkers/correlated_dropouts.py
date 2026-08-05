# checkers/correlated_dropouts.py

from datetime import datetime, timezone, timedelta

HOURS_TO_CHECK = 48
WINDOW_MINUTES = 3          # devices dropping within this window are "correlated"
MIN_DEVICES_IN_WINDOW = 3   # need at least this many to flag a correlated event
PHYSICAL_DOMAINS = ["light", "switch", "binary_sensor", "sensor"]
BUCKET_MINUTES = 10         # floor window_start to this many minutes for stable IDs

# Integrations whose entities go unavailable for non-hardware reasons
# (phone leaves home, app disconnects, etc.) and must be excluded from
# dropout detection to avoid false correlated-dropout alerts.
COMPANION_INTEGRATIONS = frozenset({
    "mobile_app",  # HA companion app (iOS and Android)
    "ios",         # legacy iOS integration
})


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
            return local_dt.strftime(f'%Y-%m-%d %-I:%M %p {abbr}').replace('AM', 'am').replace('PM', 'pm')
    except Exception:
        pass
    # Fallback: display UTC explicitly
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime('%Y-%m-%d %-I:%M %p UTC').replace('AM', 'am').replace('PM', 'pm')


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


async def check_correlated_dropouts(supervisor, ha_timezone: str | None = None) -> list:
    """
    Detects when multiple distinct devices went unavailable within the same
    short time window — a signal of a network or hub event rather than a
    single flaky device.

    Groups all windows that fall in the same BUCKET_MINUTES time bucket into a
    single issue.  The issue ID is derived from the bucket epoch (window_start
    floored to BUCKET_MINUTES), NOT from the exact device set.  This makes IDs
    stable across re-detections of the same underlying event even when a
    slightly different device set is observed between pushes (e.g. 38 vs 40
    devices for the same hub restart), so user dismissals persist correctly.

    Companion-app entities (mobile_app / ios) are excluded before history is
    fetched: phone sensors going unavailable when someone leaves home are not
    hardware dropouts and must not inflate correlated-dropout counts.

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

    # Fetch companion-app entity IDs to exclude from dropout detection.
    companion_ids = await _get_companion_entity_ids(supervisor)

    candidates = [
        s for s in states
        if _is_physical_entity(s.get("entity_id", ""), companion_ids)
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

    # Group windows by time bucket — same bucket = same underlying event,
    # even if the exact device set varies slightly between pushes.
    # Key: bucket_epoch (window_start floored to BUCKET_MINUTES) → {entities, timestamps}
    _bucket_secs = BUCKET_MINUTES * 60
    groups: dict[int, dict] = {}
    for window_start, entities in windows:
        bucket_epoch = int(window_start.timestamp()) // _bucket_secs * _bucket_secs
        if bucket_epoch not in groups:
            groups[bucket_epoch] = {'entities': set(), 'timestamps': []}
        groups[bucket_epoch]['entities'].update(entities)
        groups[bucket_epoch]['timestamps'].append(window_start)

    issues = []
    for bucket_epoch, data in groups.items():
        sorted_entities = sorted(data['entities'])
        timestamps = data['timestamps']
        # Stable ID: 10-minute bucket epoch of the earliest window start.
        # Survives small device-set variations between pushes for the same
        # underlying event (e.g. hub restart seen as 38 vs 40 devices).
        issue_id = f"correlated_dropout_{bucket_epoch}"

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
    skip_keywords = ["group", "all", "area", "floor", "zone"]
    name_part = entity_id.split(".")[1] if "." in entity_id else ""
    return not any(kw in name_part.lower() for kw in skip_keywords)


def _parse(when: str):
    try:
        return datetime.fromisoformat(when)
    except Exception:
        return None
