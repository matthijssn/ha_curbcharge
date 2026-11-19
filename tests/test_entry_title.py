"""Tests for safe CurbCharge config-entry titles."""

import pytest
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE

from custom_components.curbcharge.const import (
    CONF_ENTRY_NAME,
    CONF_LOCATION_ENTITY,
    CONF_LOCATION_SOURCE,
    CONF_PROVIDER,
    LOCATION_ENTITY,
    LOCATION_HOME,
    LOCATION_MANUAL,
    PROVIDER_AFDC_US,
    PROVIDER_NDW_DOT_NL,
)
from custom_components.curbcharge.entry_title import entry_title


def test_generated_home_title_uses_provider_name(hass):
    assert (
        entry_title(
            hass,
            {
                CONF_LOCATION_SOURCE: LOCATION_HOME,
                CONF_PROVIDER: PROVIDER_AFDC_US,
            },
        )
        == "CurbCharge - AFDC United States (Home)"
    )


def test_generated_manual_title_does_not_expose_coordinates(hass):
    title = entry_title(
        hass,
        {
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_LATITUDE: 52.123456,
            CONF_LONGITUDE: 5.654321,
        },
    )

    assert title == "CurbCharge - NDW DOT-NL (Manual location)"
    assert "52.123456" not in title
    assert "5.654321" not in title


@pytest.mark.parametrize(
    ("friendly_name", "expected_location"),
    (("Vehicle location", "Vehicle location"), (None, "sensor.vehicle_location")),
)
def test_generated_entity_title_uses_friendly_name_or_entity_id(
    hass, friendly_name, expected_location
):
    entity_id = "sensor.vehicle_location"
    attributes = {CONF_LATITUDE: 52.1, CONF_LONGITUDE: 5.1}
    if friendly_name is not None:
        attributes["friendly_name"] = friendly_name
    hass.states.async_set(entity_id, "home", attributes)

    assert (
        entry_title(
            hass,
            {
                CONF_LOCATION_SOURCE: LOCATION_ENTITY,
                CONF_LOCATION_ENTITY: entity_id,
                CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            },
        )
        == f"CurbCharge - NDW DOT-NL ({expected_location})"
    )


def test_custom_entry_name_overrides_and_can_clear_generated_title(hass):
    config = {
        CONF_LOCATION_SOURCE: LOCATION_HOME,
        CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        CONF_ENTRY_NAME: "  Garage chargers  ",
    }

    assert entry_title(hass, config) == "Garage chargers"
    config[CONF_ENTRY_NAME] = ""
    assert entry_title(hass, config) == "CurbCharge - NDW DOT-NL (Home)"


def test_unknown_provider_uses_safe_fallback(hass):
    assert (
        entry_title(
            hass,
            {CONF_LOCATION_SOURCE: LOCATION_HOME, CONF_PROVIDER: "unknown"},
        )
        == "CurbCharge - Charging data (Home)"
    )
    assert (
        entry_title(
            hass,
            {
                CONF_LOCATION_SOURCE: LOCATION_HOME,
                CONF_PROVIDER: 123,
                CONF_ENTRY_NAME: 42,
            },
        )
        == "CurbCharge - Charging data (Home)"
    )
