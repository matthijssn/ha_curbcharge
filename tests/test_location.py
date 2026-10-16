"""Tests for search location source resolution."""

import pytest
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE

from custom_components.curbcharge.const import (
    CONF_LOCATION_ENTITY,
    CONF_LOCATION_SOURCE,
    LOCATION_ENTITY,
    LOCATION_HOME,
    LOCATION_MANUAL,
)
from custom_components.curbcharge.location import resolve_location, valid_coordinates


def test_resolve_home_and_manual_locations_preserves_existing_behavior(hass):
    hass.config.latitude = 52.1
    hass.config.longitude = 5.1

    assert resolve_location(hass, {CONF_LOCATION_SOURCE: LOCATION_HOME}) == (
        52.1,
        5.1,
    )
    assert resolve_location(hass, {CONF_LOCATION_SOURCE: LOCATION_ENTITY}) is None
    assert (
        resolve_location(
            hass,
            {
                CONF_LOCATION_SOURCE: LOCATION_ENTITY,
                CONF_LOCATION_ENTITY: True,
            },
        )
        is None
    )
    assert resolve_location(
        hass,
        {
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_LATITUDE: 0,
            CONF_LONGITUDE: 0,
        },
    ) == (0.0, 0.0)

    hass.config.latitude = 0
    hass.config.longitude = 0
    assert resolve_location(hass, {CONF_LOCATION_SOURCE: LOCATION_HOME}) is None


def test_resolve_any_entity_with_valid_location_attributes(hass):
    hass.states.async_set(
        "sensor.vehicle_position",
        "moving",
        {CONF_LATITUDE: 0, CONF_LONGITUDE: 0},
    )

    assert resolve_location(
        hass,
        {
            CONF_LOCATION_SOURCE: LOCATION_ENTITY,
            CONF_LOCATION_ENTITY: "sensor.vehicle_position",
        },
    ) == (0.0, 0.0)


@pytest.mark.parametrize("state", ["unknown", "unavailable"])
def test_resolve_entity_rejects_unknown_or_unavailable_state(hass, state):
    hass.states.async_set(
        "sensor.vehicle_position",
        state,
        {CONF_LATITUDE: 52.0, CONF_LONGITUDE: 5.0},
    )

    assert (
        resolve_location(
            hass,
            {
                CONF_LOCATION_SOURCE: LOCATION_ENTITY,
                CONF_LOCATION_ENTITY: "sensor.vehicle_position",
            },
        )
        is None
    )


@pytest.mark.parametrize(
    ("attributes", "entity_id"),
    [
        ({CONF_LATITUDE: True, CONF_LONGITUDE: 5.0}, "sensor.bool"),
        ({CONF_LATITUDE: float("nan"), CONF_LONGITUDE: 5.0}, "sensor.nan"),
        ({CONF_LATITUDE: 91.0, CONF_LONGITUDE: 5.0}, "sensor.latitude"),
        ({CONF_LATITUDE: 52.0, CONF_LONGITUDE: 181.0}, "sensor.longitude"),
        ({CONF_LATITUDE: 52.0}, "sensor.missing"),
    ],
)
def test_resolve_entity_rejects_invalid_coordinate_attributes(
    hass, attributes, entity_id
):
    hass.states.async_set(entity_id, "active", attributes)

    assert (
        resolve_location(
            hass,
            {CONF_LOCATION_SOURCE: LOCATION_ENTITY, CONF_LOCATION_ENTITY: entity_id},
        )
        is None
    )


def test_entity_resolution_rejects_missing_entity_and_invalid_source(hass):
    assert (
        resolve_location(
            hass,
            {
                CONF_LOCATION_SOURCE: LOCATION_ENTITY,
                CONF_LOCATION_ENTITY: "sensor.not_created",
            },
        )
        is None
    )
    assert resolve_location(hass, {CONF_LOCATION_SOURCE: "unsupported"}) is None


def test_valid_coordinates_rejects_boolean_and_non_finite_values():
    assert valid_coordinates(True, 5.0) is None
    assert valid_coordinates(52.0, float("inf")) is None
    assert valid_coordinates(52.0, 5.0) == (52.0, 5.0)
