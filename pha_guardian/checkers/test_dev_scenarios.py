import asyncio

from checkers.correlated_dropouts import check_correlated_dropouts
from checkers.device_dropouts import check_device_dropouts
from dev_scenarios import ScenarioSupervisor


def _run(coro):
    return asyncio.run(coro)


def test_healthy_scenario_has_no_dropout_issues():
    supervisor = ScenarioSupervisor("healthy")
    assert _run(check_device_dropouts(supervisor)) == []
    assert _run(check_correlated_dropouts(supervisor)) == []


def test_device_dropout_scenario_uses_physical_device_name():
    supervisor = ScenarioSupervisor("device_dropout")
    issues = _run(check_device_dropouts(supervisor))
    assert len(issues) == 1
    assert issues[0]["id"] == "dropout_device_device-kitchen"
    assert "Kitchen Pendant" in issues[0]["title"]


def test_correlated_scenario_excludes_phone_sensor():
    supervisor = ScenarioSupervisor("correlated_dropout")
    issues = _run(check_correlated_dropouts(supervisor))
    assert len(issues) == 1
    assert len(issues[0]["involved_entities"]) == 3
    assert "sensor.dave_phone_battery" not in issues[0]["involved_entities"]