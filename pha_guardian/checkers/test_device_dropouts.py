"""
Unit tests for device_dropouts.py — specifically the device-grouping feature
that collapses multiple entities sharing the same HA device_id into a single
issue card rather than one card per entity.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

import pytest

from .device_dropouts import (
    check_device_dropouts,
    _is_physical_entity,
    _extract_dropout_events,
    DROPOUT_THRESHOLD,
    HOURS_TO_CHECK,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(hours_ago: int) -> str:
    """Return a UTC ISO-8601 string for N hours ago."""
    dt = datetime(2026, 7, 29, 12, 0, 0, tzinfo=timezone.utc) - timedelta(hours=hours_ago)
    return dt.isoformat()


def _make_unavailable_history(entity_id: str, count: int, start_hours_ago: int = 40) -> list:
    """
    Build a minimal state history list with `count` unavailable→available cycles
    so _extract_dropout_events() returns exactly `count` events.
    entity_id is required because the checker filters entries by entity_id.
    """
    entries = []
    for i in range(count):
        base = start_hours_ago - i * 2
        entries.append({"entity_id": entity_id, "state": "unavailable", "when": _ts(base)})
        entries.append({"entity_id": entity_id, "state": "on",          "when": _ts(base - 1)})
    return entries


def _make_supervisor(states, history_map, entity_registry=None, device_registry=None):
    """
    Build a minimal mock supervisor.

    states          — list of {"entity_id": str, "attributes": {...}} dicts
    history_map     — dict mapping entity_id → state-history list
    entity_registry — list of {"entity_id": str, "platform": str,
                                "device_id": str|None} dicts
    device_registry — list of {"id": str, "name": str} dicts
    """
    supervisor = AsyncMock()

    def _get_core_side_effect(path):
        if path == "/states":
            return states
        if path == "/config/device_registry/list":
            return device_registry or []
        return []

    supervisor._get_core = AsyncMock(side_effect=_get_core_side_effect)
    supervisor.get_history = AsyncMock(
        side_effect=lambda eid, hours: history_map.get(eid, [])
    )
    supervisor.get_entity_registry = AsyncMock(return_value=entity_registry or [])
    return supervisor


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Device-grouping tests
# ---------------------------------------------------------------------------

class TestDeviceGrouping:
    """Two entities sharing a device_id → one consolidated issue."""

    def test_two_entities_same_device_produce_one_issue(self):
        """The two Sonos switch entities collapse into a single issue."""
        entity_registry = [
            {"entity_id": "switch.master_bedroom_loudness",  "platform": "sonos", "device_id": "dev-abc"},
            {"entity_id": "switch.master_bedroom_crossfade", "platform": "sonos", "device_id": "dev-abc"},
        ]
        device_registry = [
            {"id": "dev-abc", "name": "Master Bedroom Speaker"},
        ]
        states = [
            {"entity_id": "switch.master_bedroom_loudness",  "attributes": {"friendly_name": "Master Bedroom Loudness"}},
            {"entity_id": "switch.master_bedroom_crossfade", "attributes": {"friendly_name": "Master Bedroom Crossfade"}},
        ]
        history = {
            "switch.master_bedroom_loudness":  _make_unavailable_history("switch.master_bedroom_loudness", 14),
            "switch.master_bedroom_crossfade": _make_unavailable_history("switch.master_bedroom_crossfade", 14),
        }

        supervisor = _make_supervisor(states, history, entity_registry, device_registry)
        issues = _run(check_device_dropouts(supervisor))

        assert len(issues) == 1
        issue = issues[0]
        assert issue["id"] == "dropout_device_dev-abc"
        assert "Master Bedroom Speaker" in issue["title"]
        assert set(issue["entity_ids"]) == {
            "switch.master_bedroom_loudness",
            "switch.master_bedroom_crossfade",
        }

    def test_different_devices_produce_separate_issues(self):
        """Entities on different devices each get their own issue."""
        entity_registry = [
            {"entity_id": "switch.master_bedroom_loudness", "platform": "sonos", "device_id": "dev-mbr"},
            {"entity_id": "switch.living_room_loudness",    "platform": "sonos", "device_id": "dev-lr"},
        ]
        device_registry = [
            {"id": "dev-mbr", "name": "Master Bedroom Speaker"},
            {"id": "dev-lr",  "name": "Living Room Speaker"},
        ]
        states = [
            {"entity_id": "switch.master_bedroom_loudness", "attributes": {}},
            {"entity_id": "switch.living_room_loudness",    "attributes": {}},
        ]
        history = {
            "switch.master_bedroom_loudness": _make_unavailable_history("switch.master_bedroom_loudness", 6),
            "switch.living_room_loudness":    _make_unavailable_history("switch.living_room_loudness", 6),
        }

        supervisor = _make_supervisor(states, history, entity_registry, device_registry)
        issues = _run(check_device_dropouts(supervisor))

        assert len(issues) == 2
        ids = {i["id"] for i in issues}
        assert "dropout_device_dev-mbr" in ids
        assert "dropout_device_dev-lr"  in ids

    def test_device_name_falls_back_to_friendly_name_when_registry_unavailable(self):
        """If device registry is down, fall back to entity friendly_name."""
        entity_registry = [
            {"entity_id": "switch.living_room_loudness", "platform": "sonos", "device_id": "dev-lr"},
        ]
        # No device registry — device_registry stays empty
        states = [
            {"entity_id": "switch.living_room_loudness",
             "attributes": {"friendly_name": "Living Room Loudness"}},
        ]
        history = {
            "switch.living_room_loudness": _make_unavailable_history("switch.living_room_loudness", 5),
        }

        supervisor = _make_supervisor(states, history, entity_registry, device_registry=None)
        issues = _run(check_device_dropouts(supervisor))

        assert len(issues) == 1
        # Title should mention something useful even without a device name
        assert issues[0]["id"] == "dropout_device_dev-lr"
        assert "Living Room Loudness" in issues[0]["title"]

    def test_entity_with_no_device_id_becomes_orphan_issue(self):
        """Entity not in device registry (no device_id) → per-entity issue."""
        entity_registry = [
            {"entity_id": "sensor.some_template_sensor", "platform": "template", "device_id": None},
        ]
        states = [
            {"entity_id": "sensor.some_template_sensor",
             "attributes": {"friendly_name": "Template Sensor"}},
        ]
        history = {
            "sensor.some_template_sensor": _make_unavailable_history("sensor.some_template_sensor", 7),
        }

        supervisor = _make_supervisor(states, history, entity_registry)
        issues = _run(check_device_dropouts(supervisor))

        assert len(issues) == 1
        assert issues[0]["id"] == "dropout_sensor.some_template_sensor"
        assert issues[0]["entity_ids"] == ["sensor.some_template_sensor"]

    def test_entity_absent_from_registry_becomes_orphan_issue(self):
        """Entity entirely missing from entity registry → per-entity orphan."""
        # Empty registry — entity not registered at all
        states = [
            {"entity_id": "switch.mystery_device",
             "attributes": {"friendly_name": "Mystery"}},
        ]
        history = {
            "switch.mystery_device": _make_unavailable_history("switch.mystery_device", 5),
        }

        supervisor = _make_supervisor(states, history, entity_registry=[])
        issues = _run(check_device_dropouts(supervisor))

        assert len(issues) == 1
        assert issues[0]["id"] == "dropout_switch.mystery_device"

    def test_max_dropout_count_used_for_device_issue(self):
        """The device issue uses the highest entity count, not the first entity's."""
        entity_registry = [
            {"entity_id": "switch.mbr_a", "platform": "sonos", "device_id": "dev-abc"},
            {"entity_id": "switch.mbr_b", "platform": "sonos", "device_id": "dev-abc"},
        ]
        states = [
            {"entity_id": "switch.mbr_a", "attributes": {}},
            {"entity_id": "switch.mbr_b", "attributes": {}},
        ]
        history = {
            "switch.mbr_a": _make_unavailable_history("switch.mbr_a", 5),   # just at threshold
            "switch.mbr_b": _make_unavailable_history("switch.mbr_b", 14),  # higher
        }

        supervisor = _make_supervisor(states, history, entity_registry)
        issues = _run(check_device_dropouts(supervisor))

        assert len(issues) == 1
        assert issues[0]["dropout_count"] == 14

    def test_only_one_entity_above_threshold_still_groups(self):
        """If only one member entity hits the threshold, the device group still forms."""
        entity_registry = [
            {"entity_id": "switch.mbr_loud",   "platform": "sonos", "device_id": "dev-abc"},
            {"entity_id": "switch.mbr_cross",  "platform": "sonos", "device_id": "dev-abc"},
        ]
        states = [
            {"entity_id": "switch.mbr_loud",  "attributes": {}},
            {"entity_id": "switch.mbr_cross", "attributes": {}},
        ]
        history = {
            "switch.mbr_loud":  _make_unavailable_history("switch.mbr_loud", 6),  # above threshold
            "switch.mbr_cross": _make_unavailable_history("switch.mbr_cross", 2),  # below threshold — not in results
        }

        supervisor = _make_supervisor(states, history, entity_registry)
        issues = _run(check_device_dropouts(supervisor))

        # Only mbr_loud cleared the threshold; mbr_cross is absent from entity_results
        # → device group has one member → single issue
        assert len(issues) == 1
        assert issues[0]["id"] == "dropout_device_dev-abc"
        assert issues[0]["entity_ids"] == ["switch.mbr_loud"]

    def test_companion_app_entities_excluded_before_grouping(self):
        """mobile_app entities are excluded regardless of device_id."""
        entity_registry = [
            {"entity_id": "sensor.iphone_bssid",  "platform": "mobile_app", "device_id": "dev-phone"},
            {"entity_id": "sensor.iphone_battery", "platform": "mobile_app", "device_id": "dev-phone"},
        ]
        states = [
            {"entity_id": "sensor.iphone_bssid",   "attributes": {}},
            {"entity_id": "sensor.iphone_battery",  "attributes": {}},
        ]
        history = {
            "sensor.iphone_bssid":   _make_unavailable_history("sensor.iphone_bssid", 10),
            "sensor.iphone_battery": _make_unavailable_history("sensor.iphone_battery", 10),
        }

        supervisor = _make_supervisor(states, history, entity_registry)
        issues = _run(check_device_dropouts(supervisor))

        assert issues == []


# ---------------------------------------------------------------------------
# Existing unit tests preserved
# ---------------------------------------------------------------------------

class TestIsPhysicalEntity:
    def test_accepts_light(self):
        assert _is_physical_entity("light.kitchen") is True

    def test_rejects_group(self):
        assert _is_physical_entity("light.all_lights") is False

    def test_rejects_non_physical_domain(self):
        assert _is_physical_entity("person.john") is False

    def test_rejects_skip_ids(self):
        assert _is_physical_entity("sensor.foo", frozenset({"sensor.foo"})) is False


class TestExtractDropoutEvents:
    def test_single_dropout(self):
        entries = [
            {"entity_id": "light.x", "state": "on",          "when": _ts(10)},
            {"entity_id": "light.x", "state": "unavailable", "when": _ts(9)},
            {"entity_id": "light.x", "state": "on",          "when": _ts(8)},
        ]
        events = _extract_dropout_events(entries)
        assert len(events) == 1
        assert events[0]["ended_at"] is not None

    def test_still_down_produces_null_ended_at(self):
        entries = [
            {"entity_id": "light.x", "state": "on",          "when": _ts(5)},
            {"entity_id": "light.x", "state": "unavailable", "when": _ts(1)},
        ]
        events = _extract_dropout_events(entries)
        assert len(events) == 1
        assert events[0]["ended_at"] is None
        assert events[0]["duration_minutes"] is None
