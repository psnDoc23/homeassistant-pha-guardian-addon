"""
Unit tests for correlated_dropouts.py — specifically the stable bucket-based
ID generation introduced to fix dismissed issues reappearing on every push.
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


def _make_supervisor(states, history_map):
    """
    Build a minimal mock supervisor.

    states      — list of {"entity_id": str} dicts
    history_map — dict mapping entity_id → list of {"state": str, "when": str}
    """
    supervisor = AsyncMock()
    supervisor._get_core = AsyncMock(return_value=states)
    supervisor.get_history = AsyncMock(
        side_effect=lambda eid, hours: history_map.get(eid, [])
    )
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
