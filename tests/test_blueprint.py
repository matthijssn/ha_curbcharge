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
NEARBY_BLUEPRINT_PATH = BLUEPRINT_PATH.with_name(
    "notify_when_nearby_station_becomes_available.yaml"
)
FAVORITE_DATA_BLUEPRINT_PATH = BLUEPRINT_PATH.with_name(
    "notify_when_favorite_data_needs_attention.yaml"
)
FAVORITE_TARIFF_BLUEPRINT_PATH = BLUEPRINT_PATH.with_name(
    "notify_when_favorite_tariff_crosses_threshold.yaml"
)


def _blueprint(path: Path = BLUEPRINT_PATH) -> Blueprint:
    return Blueprint(
        yaml_util.load_yaml_dict(path),
        path=str(path),
        expected_domain="automation",
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )


def _automation_config(
    blueprint: Blueprint, input_overrides: dict[str, object] | None = None
) -> dict:
    inputs = {
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
    defaults = {
        "station_entities": ["sensor.charger_status_summary"],
        "available_stations_entity": "sensor.nearby_available_stations",
        "top_nearby_entity": "sensor.top_nearby_chargers",
        "favorite_summary_entity": "sensor.favorite_summary",
        "favorite_status_entities": ["sensor.favorite_status"],
        "price_threshold": 0.35,
        "currency": "EUR",
    }
    inputs.update(
        {key: value for key, value in defaults.items() if key in blueprint.inputs}
    )
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


def _state(state: str, attributes: dict[str, object] | None = None):
    return {"state": state, "attributes": attributes or {}}


def _condition_template(automation: dict) -> str:
    return automation["conditions"][0]["value_template"].template


def _render_condition(hass, automation: dict, variables: dict[str, object]) -> str:
    return Template(_condition_template(automation), hass).async_render(variables)


@pytest.mark.asyncio
async def test_blueprint_parses_and_only_triggers_on_known_unavailable_to_available(
    hass,
):
    blueprint = _blueprint()
    automation = _automation_config(blueprint)

    assert blueprint.domain == "automation"
    assert blueprint.inputs["availability_transition"]["default"] == (
        "becomes_available"
    )
    assert len(automation["triggers"]) == 1
    trigger = automation["triggers"][0]
    assert trigger["platform"] == "state"
    assert trigger["entity_id"] == ["sensor.charger_status_summary"]
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
async def test_availability_blueprint_only_matches_selected_known_transitions(hass):
    automation = _automation_config(_blueprint())
    template = _condition_template(automation)
    condition = Template(template, hass)

    async def allows(previous: str | None, current: str, transition: str) -> str:
        return await condition.async_render(
            {
                "availability_transition": transition,
                "trigger": {
                    "from_state": None if previous is None else _state(previous),
                    "to_state": _state(current),
                },
            }
        )

    assert await allows("none_available", "available", "becomes_available")
    assert not await allows("available", "none_available", "becomes_available")
    assert await allows("available", "none_available", "becomes_unavailable")
    assert await allows("none_available", "available", "any_known_change")
    assert await allows("available", "none_available", "any_known_change")
    assert not await allows("unknown", "available", "any_known_change")
    assert not await allows("unavailable", "available", "any_known_change")
    assert not await allows(None, "available", "any_known_change")


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

    async def allows(attributes: dict[str, object], *, enabled: bool) -> bool:
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

    assert await allows({}, enabled=False)
    assert await allows({"last_updated": recent_timestamp}, enabled=True)
    assert not await allows({}, enabled=True)
    assert not await allows({"last_updated": "invalid timestamp"}, enabled=True)
    assert not await allows({"last_updated": future_timestamp}, enabled=True)
    assert not await allows({"last_updated": old_timestamp}, enabled=True)
    assert await allows({"provider_data_age_seconds": 300}, enabled=True)
    assert not await allows({"provider_data_age_seconds": 700}, enabled=True)
    assert not await allows({"provider_data_age_seconds": -1}, enabled=True)


@pytest.mark.asyncio
async def test_nearby_availability_blueprint_only_triggers_on_numeric_increases(hass):
    blueprint = _blueprint(NEARBY_BLUEPRINT_PATH)
    automation = _automation_config(blueprint)

    assert len(automation["triggers"]) == 1
    assert automation["triggers"][0]["entity_id"] == [
        "sensor.nearby_available_stations"
    ]

    async def allows(previous: str | None, current: str) -> str:
        return await _render_condition(
            hass,
            automation,
            {
                "trigger": {
                    "from_state": None if previous is None else _state(previous),
                    "to_state": _state(current),
                }
            },
        )

    assert await allows("0", "1")
    assert await allows("1", "3")
    assert not await allows("1", "1")
    assert not await allows("2", "1")
    assert not await allows("unknown", "1")
    assert not await allows("unavailable", "1")
    assert not await allows(None, "1")


@pytest.mark.asyncio
async def test_favorite_data_blueprint_distinguishes_stale_and_failed_updates(hass):
    blueprint = _blueprint(FAVORITE_DATA_BLUEPRINT_PATH)
    automation = _automation_config(blueprint)
    condition = Template(_condition_template(automation), hass)

    async def allows(
        previous: str | None,
        current: str,
        previous_attributes: dict[str, object],
        current_attributes: dict[str, object],
    ) -> str:
        return await condition.async_render(
            {
                "trigger": {
                    "from_state": (
                        None
                        if previous is None
                        else _state(previous, previous_attributes)
                    ),
                    "to_state": _state(current, current_attributes),
                }
            }
        )

    current = {
        "configured_count": 2,
        "provider_update_success": True,
        "last_successful_update": "2026-01-01T00:00:00+00:00",
        "stale_count": 0,
        "missing_count": 0,
    }
    assert await allows(
        "2",
        "2",
        current,
        {**current, "stale_count": 1},
    )
    assert await allows(
        "2",
        "2",
        {**current, "stale_count": 1},
        {**current, "stale_count": 1, "missing_count": 1},
    )
    assert await allows(
        "2",
        "unavailable",
        current,
        {
            "configured_count": 2,
            "provider_update_success": False,
            "last_successful_update": current["last_successful_update"],
        },
    )
    assert not await allows(
        "unknown",
        "unavailable",
        {"configured_count": 2, "provider_update_success": False},
        {
            "configured_count": 2,
            "provider_update_success": False,
            "last_successful_update": None,
        },
    )
    assert not await allows(
        "unavailable",
        "unavailable",
        {
            "configured_count": 2,
            "provider_update_success": False,
            "last_successful_update": current["last_successful_update"],
        },
        {
            "configured_count": 2,
            "provider_update_success": False,
            "last_successful_update": current["last_successful_update"],
        },
    )
    assert not await allows(
        "2",
        "unavailable",
        {**current, "provider_update_success": False},
        {
            "configured_count": 2,
            "provider_update_success": False,
            "last_successful_update": current["last_successful_update"],
        },
    )
    assert not await allows("2", "2", current, current)
    assert not await allows(
        "unavailable",
        "2",
        {"configured_count": 2, "provider_update_success": False},
        {**current, "stale_count": 1},
    )


@pytest.mark.asyncio
async def test_favorite_tariff_blueprint_only_alerts_on_valid_currency_crossing(hass):
    blueprint = _blueprint(FAVORITE_TARIFF_BLUEPRINT_PATH)
    automation = _automation_config(blueprint)
    template = Template(_condition_template(automation), hass)

    async def allows(
        previous: dict[str, object] | None,
        current: dict[str, object],
        *,
        direction: str = "below",
    ) -> str:
        return await template.async_render(
            {
                "price_threshold": 0.35,
                "currency": "EUR",
                "threshold_direction": direction,
                "trigger": {
                    "from_state": None
                    if previous is None
                    else _state("available", previous),
                    "to_state": _state("available", current),
                },
            }
        )

    def tariff(price: float, currency: str = "EUR", status: str = "available"):
        return {
            "price_per_kwh": price,
            "currency": currency,
            "tariff_status": status,
            "provider_update_success": True,
            "is_stale": False,
        }

    assert await allows(tariff(0.40), tariff(0.30))
    assert not await allows(tariff(0.30), tariff(0.20))
    assert await allows(tariff(0.30), tariff(0.40), direction="above")
    assert not await allows(tariff(0.40), tariff(0.30, currency="USD"))
    assert not await allows(tariff(0.40), tariff(0.30, status="incomplete"))
    assert not await allows(None, tariff(0.30))
    assert not await allows(
        tariff(0.40), {"currency": "EUR", "tariff_status": "available"}
    )
    assert not await allows(tariff(0.40), {**tariff(0.30), "is_stale": True})
    assert not await allows(
        tariff(0.40), {**tariff(0.30), "provider_update_success": False}
    )
