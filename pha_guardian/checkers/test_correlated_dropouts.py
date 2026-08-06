"""
Unit tests for correlated_dropouts.py — specifically the stable bucket-based
ID generation introduced to fix dismissed issues reappearing on every push,
and companion-app entity exclusion to prevent phone sensors from inflating
correlated-dropout counts.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock

import pytest

from .correlated_dropouts import check_correlated_dropouts, BUCKET_MINUTES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(hour: int, minute: int, second: int = 0) -> str:
    """Return a UTC ISO-8601 string for a fixed date at the given H:M:S."""
    dt = datetime(2026, 7, 29, hour, minute, second, tzinfo=timezone.utc)
    return dt.isoformat()


def _make_supervisor(states, history_map, registry=None, device_registry=None):
    """
    Build a minimal mock supervisor.

    states          — list of {"entity_id": str} dicts
    history_map     — dict mapping entity_id → list of {"state": str, "when": str}
    registry        — list of {"entity_id": str, "platform": str, "device_id": str}
                      dicts for the entity registry (defaults to empty list)
    device_registry — list of {"id": str, "name": str, "entry_type": str|None}
                      dicts for the device registry (defaults to empty list)
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
    supervisor.get_entity_registry = AsyncMock(return_value=registry or [])
    return supervisor


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Stable ID tests
# ---------------------------------------------------------------------------

class TestBucketBasedStableIDs:
    """
    Core property: re-detecting the same underlying event with a slightly
    different device set must produce the same issue ID so that dismissals
    survive the next push.
    """

    # A hub restart around 15:16 UTC — two pushes detect different device sets
    # but the same time bucket (15:10–15:20).

    # Push 1: 4 devices drop at 15:16
    PUSH1_DEVICES = [
        "light.living_room", "light.kitchen", "switch.fan", "sensor.temp",
    ]
    # Push 2: 3 of the same devices (one reconnected earlier) drop at 15:16:30
    PUSH2_DEVICES = [
        "light.living_room", "light.kitchen", "switch.fan",
    ]

    DROP_TIME_1 = "2026-07-29T15:16:00+00:00"
    DROP_TIME_2 = "2026-07-29T15:16:30+00:00"

    def _states(self, devices):
        return [{"entity_id": d} for d in devices]

    def _history(self, devices, drop_time):
        return {d: [{"state": "unavailable", "when": drop_time}] for d in devices}

    def test_same_bucket_same_id_different_device_set(self):
        """
        Two pushes with the same ~15:16 event but different device sets must
        produce the same issue ID so a dismissal from push-1 suppresses push-2.
        """
        sup1 = _make_supervisor(
            self._states(self.PUSH1_DEVICES),
            self._history(self.PUSH1_DEVICES, self.DROP_TIME_1),
        )
        issues1 = _run(check_correlated_dropouts(sup1))

        sup2 = _make_supervisor(
            self._states(self.PUSH2_DEVICES),
            self._history(self.PUSH2_DEVICES, self.DROP_TIME_2),
        )
        issues2 = _run(check_correlated_dropouts(sup2))

        # Both pushes must yield exactly one correlated dropout issue.
        correlated1 = [i for i in issues1 if i["id"].startswith("correlated_dropout_")]
        correlated2 = [i for i in issues2 if i["id"].startswith("correlated_dropout_")]
        assert len(correlated1) == 1, f"Expected 1 issue in push1, got {correlated1}"
        assert len(correlated2) == 1, f"Expected 1 issue in push2, got {correlated2}"

        # The IDs must be identical.
        assert correlated1[0]["id"] == correlated2[0]["id"], (
            f"ID changed between pushes: {correlated1[0]['id']} vs {correlated2[0]['id']}"
        )

    def test_id_format_is_bucket_epoch(self):
        """
        The ID suffix must be a plain integer (Unix epoch floored to
        BUCKET_MINUTES), not a hex hash.
        """
        sup = _make_supervisor(
            self._states(self.PUSH1_DEVICES),
            self._history(self.PUSH1_DEVICES, self.DROP_TIME_1),
        )
        issues = _run(check_correlated_dropouts(sup))
        correlated = [i for i in issues if i["id"].startswith("correlated_dropout_")]
        assert len(correlated) == 1

        suffix = correlated[0]["id"][len("correlated_dropout_"):]
        assert suffix.isdigit(), f"Expected numeric suffix, got: {suffix!r}"

        # Suffix must be divisible by BUCKET_MINUTES * 60 (floored to bucket).
        bucket_secs = BUCKET_MINUTES * 60
        assert int(suffix) % bucket_secs == 0, (
            f"Suffix {suffix} is not floored to {bucket_secs}s bucket"
        )

    def test_events_in_different_buckets_get_different_ids(self):
        """
        Two genuinely separate events more than BUCKET_MINUTES apart must
        still produce different IDs.
        """
        early_devices = ["light.a", "light.b", "light.c"]
        late_devices  = ["switch.x", "switch.y", "switch.z"]

        early_time = "2026-07-29T10:05:00+00:00"   # bucket: 10:00
        late_time  = "2026-07-29T11:20:00+00:00"   # bucket: 11:20  (>10 min gap)

        states = self._states(early_devices + late_devices)
        history = {
            **{d: [{"state": "unavailable", "when": early_time}] for d in early_devices},
            **{d: [{"state": "unavailable", "when": late_time}]  for d in late_devices},
        }
        sup = _make_supervisor(states, history)
        issues = _run(check_correlated_dropouts(sup))
        correlated = [i for i in issues if i["id"].startswith("correlated_dropout_")]
        assert len(correlated) == 2, f"Expected 2 issues, got: {[i['id'] for i in correlated]}"
        assert correlated[0]["id"] != correlated[1]["id"]


# ---------------------------------------------------------------------------
# Companion-app entity exclusion tests
# ---------------------------------------------------------------------------

class TestCompanionAppExclusion:
    """
    Phone sensors (mobile_app / ios integration) go unavailable when the owner
    leaves home, not due to a hardware or network fault.  They must be excluded
    from correlated-dropout detection so they don't inflate event counts or
    pull unrelated real devices into a false alert.
    """

    DROP_TIME = "2026-07-29T08:00:00+00:00"

    def test_mobile_app_entities_excluded(self):
        """
        When phone sensors drop at the same time as real devices, only the
        real devices should appear in the correlated-dropout issue.
        """
        real_devices = ["light.kitchen", "switch.fan", "sensor.temp"]
        phone_sensors = [
            "sensor.alice_iphone_bssid",
            "sensor.alice_iphone_ssid",
            "sensor.alice_iphone_battery",
            "sensor.alice_iphone_storage",
        ]
        all_entities = real_devices + phone_sensors

        states = [{"entity_id": e} for e in all_entities]
        history = {e: [{"state": "unavailable", "when": self.DROP_TIME}] for e in all_entities}
        # Registry marks phone sensors as mobile_app
        registry = (
            [{"entity_id": e, "platform": "mobile_app"} for e in phone_sensors]
            + [{"entity_id": e, "platform": "zha"} for e in real_devices]
        )

        sup = _make_supervisor(states, history, registry=registry)
        issues = _run(check_correlated_dropouts(sup))

        correlated = [i for i in issues if i["id"].startswith("correlated_dropout_")]
        assert len(correlated) == 1

        involved = set(correlated[0]["involved_entities"])
        # Real devices must be present
        for d in real_devices:
            assert d in involved, f"Real device {d} missing from involved_entities"
        # Phone sensors must be absent
        for p in phone_sensors:
            assert p not in involved, f"Phone sensor {p} must not appear in involved_entities"

    def test_ios_integration_entities_excluded(self):
        """Entities from the legacy 'ios' integration are also excluded."""
        real_devices = ["light.a", "light.b", "switch.c"]
        ios_sensors = ["sensor.bob_iphone_battery", "sensor.bob_iphone_ssid"]
        all_entities = real_devices + ios_sensors

        states = [{"entity_id": e} for e in all_entities]
        history = {e: [{"state": "unavailable", "when": self.DROP_TIME}] for e in all_entities}
        registry = (
            [{"entity_id": e, "platform": "ios"} for e in ios_sensors]
            + [{"entity_id": e, "platform": "zha"} for e in real_devices]
        )

        sup = _make_supervisor(states, history, registry=registry)
        issues = _run(check_correlated_dropouts(sup))

        correlated = [i for i in issues if i["id"].startswith("correlated_dropout_")]
        assert len(correlated) == 1
        involved = set(correlated[0]["involved_entities"])
        for s in ios_sensors:
            assert s not in involved, f"iOS sensor {s} must not appear in involved_entities"

    def test_only_phone_sensors_drop_no_correlated_issue(self):
        """
        When only companion-app sensors drop (no real devices), the checker
        must not raise a correlated-dropout issue at all.
        """
        phone_sensors = [
            "sensor.alice_iphone_bssid",
            "sensor.alice_iphone_ssid",
            "sensor.alice_iphone_battery",
            "sensor.alice_iphone_storage",
            "sensor.alice_iphone_connection_type",
        ]
        states = [{"entity_id": e} for e in phone_sensors]
        history = {e: [{"state": "unavailable", "when": self.DROP_TIME}] for e in phone_sensors}
        registry = [{"entity_id": e, "platform": "mobile_app"} for e in phone_sensors]

        sup = _make_supervisor(states, history, registry=registry)
        issues = _run(check_correlated_dropouts(sup))

        correlated = [i for i in issues if i["id"].startswith("correlated_dropout_")]
        assert correlated == [], (
            f"Expected no correlated dropout when only phone sensors drop, got: {correlated}"
        )

    def test_service_type_device_excluded_from_correlated(self):
        """entry_type='service' device excluded even when not in COMPANION_INTEGRATIONS."""
        # 'some_new_app' is not in COMPANION_INTEGRATIONS — only entry_type saves us
        registry = [
            {"entity_id": f"sensor.new_app_{i}", "platform": "some_new_app",
             "device_id": "dev-svc"}
            for i in range(5)
        ]
        device_registry = [{"id": "dev-svc", "name": "New App", "entry_type": "service"}]
        states = [{"entity_id": f"sensor.new_app_{i}"} for i in range(5)]
        history_map = {
            f"sensor.new_app_{i}": [
                {"entity_id": f"sensor.new_app_{i}", "state": "unavailable", "when": _ts(1, i)}
                for _ in range(3)
            ]
            for i in range(5)
        }
        supervisor = _make_supervisor(states, history_map, registry, device_registry)
        issues = _run(check_correlated_dropouts(supervisor))
        correlated = [i for i in issues if i.get("id", "").startswith("correlated_")]
        assert correlated == [], "service-type entities should not produce correlated issues"

    def test_registry_failure_falls_back_gracefully(self):
        """
        If the entity registry endpoint is unavailable, the checker must still
        return results (falling back to including all physical entities).
        """
        devices = ["light.kitchen", "switch.fan", "sensor.temp"]
        states = [{"entity_id": d} for d in devices]
        history = {d: [{"state": "unavailable", "when": self.DROP_TIME}] for d in devices}

        sup = _make_supervisor(states, history)
        # Override get_entity_registry to raise an exception
        sup.get_entity_registry = AsyncMock(side_effect=Exception("registry unavailable"))

        # Must not raise — falls back to frozenset() and processes all entities
        issues = _run(check_correlated_dropouts(sup))
        correlated = [i for i in issues if i["id"].startswith("correlated_dropout_")]
        assert len(correlated) == 1, (
            f"Expected 1 issue with graceful fallback, got: {correlated}"
        )
