"""Tests for the CurbCharge availability notification Blueprint."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from homeassistant.components.automation.config import (
    AUTOMATION_BLUEPRINT_SCHEMA,
    PLATFORM_SCHEMA,
)
from homeassistant.components.blueprint import Blueprint, BlueprintInputs
from homeassistant.helpers.template import Template
from homeassistant.util import yaml as yaml_util

BLUEPRINT_PATH = (
    Path(__file__).parent.parent
    / "blueprints"
    / "automation"
    / "curbcharge"
    / "notify_when_charging_point_available.yaml"
)


def _blueprint() -> Blueprint:
    return Blueprint(
        yaml_util.load_yaml_dict(BLUEPRINT_PATH),
        path=str(BLUEPRINT_PATH),
        expected_domain="automation",
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )


def _automation_config(
    blueprint: Blueprint, input_overrides: dict[str, object] | None = None
) -> dict:
    inputs = {
        "station_entities": ["sensor.charger_status_summary"],
        "notification_action": [
            {
                "action": "persistent_notification.create",
                "data": {
                    "title": "{{ station_name }} is available",
                    "message": "{{ station_entity }}",
                },
            }
        ],
    }
    inputs.update(input_overrides or {})
    blueprint_inputs = BlueprintInputs(
        blueprint,
        {
            "id": "curbcharge_availability_test",
            "alias": "CurbCharge availability test",
            "use_blueprint": {"input": inputs},
        },
    )
    blueprint_inputs.validate()
    return PLATFORM_SCHEMA(blueprint_inputs.async_substitute())


@pytest.mark.asyncio
async def test_blueprint_parses_and_only_triggers_on_known_unavailable_to_available(
    hass,
):
    blueprint = _blueprint()
    automation = _automation_config(blueprint)

    assert blueprint.domain == "automation"
    assert len(automation["triggers"]) == 1
    trigger = automation["triggers"][0]
    assert trigger["platform"] == "state"
    assert trigger["entity_id"] == ["sensor.charger_status_summary"]
    assert trigger["from"] == "none_available"
    assert trigger["to"] == "available"
    assert trigger["for"] == {"hours": 0, "minutes": 0, "seconds": 0}
    assert blueprint.inputs["check_provider_data_age"]["default"] is False
    assert blueprint.inputs["maximum_provider_data_age_minutes"]["default"] == 30
    assert len(automation["actions"]) == 2
    assert (
        automation["actions"][0]["variables"].as_dict()["station_entity"].template
        == "{{ trigger.entity_id }}"
    )
    assert "station_name" in (
        automation["actions"][0]["variables"].as_dict()["station_name"].template
    )
    assert automation["actions"][1]["choose"][0]["sequence"][0]["action"] == (
        "persistent_notification.create"
    )


@pytest.mark.asyncio
async def test_blueprint_accepts_a_minimum_available_duration(hass):
    duration = {"hours": 0, "minutes": 2, "seconds": 0}
    automation = _automation_config(
        _blueprint(), {"minimum_available_duration": duration}
    )

    assert automation["triggers"][0]["for"] == duration


@pytest.mark.asyncio
async def test_optional_provider_age_guard_rejects_unknown_future_and_old_data(hass):
    automation = _automation_config(
        _blueprint(),
        {
            "check_provider_data_age": True,
            "maximum_provider_data_age_minutes": 10,
        },
    )
    action_variables = automation["variables"].as_dict()
    template_source = automation["actions"][1]["choose"][0]["conditions"][0][
        "value_template"
    ].template
    template = Template(template_source, hass)

    async def allows(timestamp: str | None, *, enabled: bool) -> bool:
        attributes = {} if timestamp is None else {"last_updated": timestamp}
        hass.states.async_set("sensor.charger_status_summary", "available", attributes)
        return template.async_render(
            {
                "trigger": {"entity_id": "sensor.charger_status_summary"},
                "check_provider_data_age": enabled
                and action_variables["check_provider_data_age"],
                "maximum_provider_data_age_minutes": action_variables[
                    "maximum_provider_data_age_minutes"
                ],
            }
        )

    now = datetime.now(UTC)
    recent_timestamp = (now - timedelta(minutes=5)).isoformat()
    old_timestamp = (now - timedelta(minutes=20)).isoformat()
    future_timestamp = (now + timedelta(minutes=1)).isoformat()

    assert await allows(None, enabled=False)
    assert await allows(recent_timestamp, enabled=True)
    assert not await allows(None, enabled=True)
    assert not await allows("invalid timestamp", enabled=True)
    assert not await allows(future_timestamp, enabled=True)
    assert not await allows(old_timestamp, enabled=True)
