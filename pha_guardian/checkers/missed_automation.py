# checkers/missed_automation.py

from datetime import datetime, timezone, timedelta

MINUTES_GRACE_PERIOD = 5


async def check_missed_automations(supervisor, automation_configs: list) -> list:
    """
    For each automation config provided, checks whether the automation's
    target entity is still 'on' after the scheduled trigger time.
    Returns a list of issues for any missed automations detected.
    """
    issues = []

    for config in automation_configs:
        result = await _check_single_automation(supervisor, config)
        if result:
            issues.append(result)

    return issues



async def _check_single_automation(supervisor, config: dict):
    """
    Checks a single automation config for a miss.
    Returns an issue dict if missed, None if not.
    """
    try:
        automation_id = config.get("id")
        alias = config.get("alias", automation_id)

        # Extract trigger time
        trigger_time_str = _get_trigger_time(config)
        if not trigger_time_str:
            return None

        # Extract target entity
        entity_id = _get_target_entity(config)
        if not entity_id:
            return None

        # Build trigger datetime for today in UTC
        trigger_dt = _build_trigger_dt(trigger_time_str)
        if not trigger_dt:
            return None

        # Only check if trigger time has passed
        now = datetime.now(timezone.utc)
        if now < trigger_dt + timedelta(minutes=MINUTES_GRACE_PERIOD):
            return None

        # Check if automation is currently enabled
        try:
            states = await supervisor._get_core("/states")
            auto_state = next(
                (s for s in states 
                 if s.get("attributes", {}).get("id") == automation_id),
                None
            )
            automation_enabled = auto_state.get("state") == "on" if auto_state else True
        except Exception:
            automation_enabled = True

        # Get logbook for the entity around the trigger time
        entries = await supervisor.get_logbook(entity_id, hours=24)

        # Find the last state change after the trigger time
        post_trigger = [
            e for e in entries
            if _parse(e["when"]) > trigger_dt
            and e.get("state") in ("on", "off")
        ]

        if not post_trigger:
            # No state changes after trigger — check current state
            state_data = await supervisor.get_state(entity_id)
            current_state = state_data.get("state")
            if current_state == "on":
                return _make_issue(alias, entity_id, trigger_time_str, automation_id, automation_enabled)
            return None

        # Check the last state after the trigger time
        last_post = post_trigger[-1]
        if last_post.get("state") == "on":
            return _make_issue(alias, entity_id, trigger_time_str, automation_id, automation_enabled)

        return None

    except Exception as e:
        return {
            "id": f"missed_automation_check_failed",
            "title": "Missed automation check failed",
            "severity": "unknown",
            "detail": str(e)
        }




def _get_trigger_time(config: dict):
    """Extracts the time trigger value from automation config."""
    for trigger in config.get("triggers", []):
        if trigger.get("trigger") == "time" and "at" in trigger:
            return trigger["at"]
    return None


def _get_target_entity(config: dict):
    """Extracts the first target entity from automation actions."""
    for action in config.get("actions", []):
        target = action.get("target", {})
        entity_id = target.get("entity_id")
        if entity_id:
            return entity_id
    return None


def _build_trigger_dt(time_str: str):
    """Builds a UTC datetime for today at the given UTC trigger time."""
    try:
        from datetime import date
        h, m, s = map(int, time_str.split(":"))
        today = date.today()
        naive_dt = datetime.combine(today, datetime.min.time().replace(hour=h, minute=m, second=s))
        return naive_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    


def _make_issue(alias: str, entity_id: str, trigger_time: str, automation_id: str, automation_enabled: bool) -> dict:
    return {
        "id": f"missed_automation_{entity_id}",
        "title": f"Missed automation: {alias}",
        "severity": "medium",
        "detail": (
            f"'{alias}' was scheduled to turn off {entity_id} at {trigger_time} "
            f"but the entity appears to still be on."
        ),
        "suggestion": (
            f"Check the automation trace in HA under Settings → Automations → "
            f"'{alias}' → Traces to see why it may have failed. Also check if "
            f"another automation or manual action is overriding it."
        ),
        "fixable": not automation_enabled,
        "automation_id": automation_id,
    }



def _parse(when: str) -> datetime:
    return datetime.fromisoformat(when)
