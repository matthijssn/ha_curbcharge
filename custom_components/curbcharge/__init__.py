"""CurbCharge Home Assistant integration."""

import math
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import create_provider
from .const import (
    CONF_INCLUDE_UNKNOWN,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH_RADIUS,
    DEFAULT_INCLUDE_UNKNOWN,
    DEFAULT_MAX_STATIONS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SEARCH_RADIUS_M,
    LOCATION_HOME,
    MAX_MAX_STATIONS,
    MAX_SEARCH_RADIUS_M,
    MIN_MAX_STATIONS,
    MIN_SCAN_INTERVAL_MINUTES,
    MIN_SEARCH_RADIUS_M,
    PLATFORMS,
    PROVIDER_NDW_DOT_NL,
)
from .const import (
    DOMAIN as DOMAIN,
)
from .coordinator import CurbChargeCoordinator
from .provider import ChargingProviderError

type CurbChargeConfigEntry = ConfigEntry[CurbChargeCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: CurbChargeConfigEntry) -> bool:
    """Set up CurbCharge from a config entry."""
    location = _entry_location(hass, entry.data)
    if location is None:
        raise ConfigEntryNotReady(
            "The configured charging search location is unavailable"
        )

    options = {**entry.data, **entry.options}
    provider_id = options.get(CONF_PROVIDER, PROVIDER_NDW_DOT_NL)
    radius_m = options.get(CONF_SEARCH_RADIUS, DEFAULT_SEARCH_RADIUS_M)
    max_stations = options.get(CONF_MAX_STATIONS, DEFAULT_MAX_STATIONS)
    scan_interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
    include_unknown = options.get(CONF_INCLUDE_UNKNOWN, DEFAULT_INCLUDE_UNKNOWN)
    if (
        provider_id != PROVIDER_NDW_DOT_NL
        or not isinstance(radius_m, int)
        or not MIN_SEARCH_RADIUS_M <= radius_m <= MAX_SEARCH_RADIUS_M
        or not isinstance(max_stations, int)
        or not MIN_MAX_STATIONS <= max_stations <= MAX_MAX_STATIONS
        or not isinstance(scan_interval, int)
        or scan_interval < MIN_SCAN_INTERVAL_MINUTES
        or not isinstance(include_unknown, bool)
    ):
        raise ConfigEntryNotReady("CurbCharge configuration is invalid")

    try:
        provider = create_provider(provider_id, async_get_clientsession(hass))
    except ChargingProviderError as err:
        raise ConfigEntryNotReady(
            "The configured charging provider is unsupported"
        ) from err

    coordinator = CurbChargeCoordinator(
        hass,
        provider,
        location[0],
        location[1],
        radius_m,
        max_stations,
        scan_interval,
        include_unknown,
        config_entry=entry,
    )
    entry.runtime_data = coordinator
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CurbChargeConfigEntry) -> bool:
    """Unload CurbCharge platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(
    hass: HomeAssistant, entry: CurbChargeConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _entry_location(
    hass: HomeAssistant, data: dict[str, Any]
) -> tuple[float, float] | None:
    is_home_location = data.get(CONF_LOCATION_SOURCE) == LOCATION_HOME
    if is_home_location:
        latitude = hass.config.latitude
        longitude = hass.config.longitude
    else:
        latitude = data.get(CONF_LATITUDE)
        longitude = data.get(CONF_LONGITUDE)
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
    if is_home_location and latitude == 0 and longitude == 0:
        return None
    return latitude, longitude
