# checkers/automation_scanner.py

SUPPORTED_DOMAINS = ["light", "switch"]
SUPPORTED_ACTIONS = ["turn_off", "turn_on"]


async def scan_automations(supervisor) -> list:
    """
    Scans all HA automations and returns ones that have time triggers
    targeting lights or switches — these are candidates for monitoring.
    """
    try:
        states = await supervisor._get_core("/states")
        automation_states = [
            s for s in states
            if s.get("entity_id", "").startswith("automation.")
            and s.get("state") == "on"  # only enabled automations
        ]

        candidates = []
        for auto_state in automation_states:
            aid = auto_state.get("attributes", {}).get("id")
            if not aid:
                continue
            try:
                config = await supervisor._get_core(f"/config/automation/config/{aid}")
                if _is_candidate(config):
                    candidates.append({
                        "id": aid,
                        "alias": config.get("alias", aid),
                        "trigger_time": _get_trigger_time(config),
                        "entity_id": _get_target_entity(config),
                    })
            except Exception:
                pass

        return candidates

    except Exception as e:
        return []


def _is_candidate(config: dict) -> bool:
    """Returns True if the automation has a time trigger and targets a light/switch."""
    has_time_trigger = any(
        t.get("trigger") == "time" and "at" in t
        for t in config.get("triggers", [])
    )
    if not has_time_trigger:
        return False

    target_entity = _get_target_entity(config)
    if not target_entity:
        return False

    domain = target_entity.split(".")[0]
    return domain in SUPPORTED_DOMAINS


def _get_trigger_time(config: dict):
    for trigger in config.get("triggers", []):
        if trigger.get("trigger") == "time" and "at" in trigger:
            return trigger["at"]
    return None


def _get_target_entity(config: dict):
    for action in config.get("actions", []):
        target = action.get("target", {})
        entity_id = target.get("entity_id")
        if entity_id:
            return entity_id
    return None