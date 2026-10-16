"""Resolve validated coordinates for a configured search location."""

import math
from collections.abc import Mapping
from typing import Any

from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LONGITUDE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant

from .const import (
    CONF_LOCATION_ENTITY,
    CONF_LOCATION_SOURCE,
    LOCATION_ENTITY,
    LOCATION_HOME,
    LOCATION_MANUAL,
)


def valid_coordinates(
    latitude: object, longitude: object
) -> tuple[float, float] | None:
    """Return finite in-range coordinates, excluding booleans."""
    if (
        not isinstance(latitude, (int, float))
        or isinstance(latitude, bool)
        or not isinstance(longitude, (int, float))
        or isinstance(longitude, bool)
    ):
        return None

    latitude = float(latitude)
    longitude = float(longitude)
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        return None
    return latitude, longitude


def resolve_location(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> tuple[float, float] | None:
    """Resolve Home, Manual, or any current entity with coordinate attributes."""
    source = data.get(CONF_LOCATION_SOURCE)
    if source == LOCATION_HOME:
        location = valid_coordinates(hass.config.latitude, hass.config.longitude)
        return None if location == (0.0, 0.0) else location
    if source in (None, LOCATION_MANUAL):
        return valid_coordinates(data.get(CONF_LATITUDE), data.get(CONF_LONGITUDE))
    if source != LOCATION_ENTITY:
        return None

    entity_id = data.get(CONF_LOCATION_ENTITY)
    if not isinstance(entity_id, str) or not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    return valid_coordinates(
        state.attributes.get(CONF_LATITUDE),
        state.attributes.get(CONF_LONGITUDE),
    )
