"""CurbCharge Home Assistant integration."""

import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import UpdateFailed

# HA 2024.12 exports at http; current Core types it in http.server.
if TYPE_CHECKING:
    from homeassistant.components.http.server import StaticPathConfig
else:
    from homeassistant.components.http import StaticPathConfig

from .api import create_provider
from .const import (
    CONF_API_TOKEN,
    CONF_FAVOURITES,
    CONF_INCLUDE_UNKNOWN,
    CONF_LOCATION_MOVEMENT_THRESHOLD,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH_RADIUS,
    CONFIG_ENTRY_VERSION,
    DEFAULT_INCLUDE_UNKNOWN,
    DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M,
    DEFAULT_MAX_STATIONS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SEARCH_RADIUS_M,
    LOCATION_ENTITY,
    MAX_MAX_STATIONS,
    MAX_SEARCH_RADIUS_M,
    MIN_MAX_STATIONS,
    MIN_SCAN_INTERVAL_MINUTES,
    MIN_SEARCH_RADIUS_M,
    PLATFORMS,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_NDW_DOT_NL,
)
from .const import (
    DOMAIN as DOMAIN,
)
from .coordinator import CurbChargeCoordinator
from .location import resolve_location
from .models import DiscoveryFilterSettings, parse_favourite_references
from .provider import ChargingProviderError
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

type CurbChargeConfigEntry = ConfigEntry[CurbChargeCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Register CurbCharge services and serve the bundled Lovelace card."""
    card_path = Path(__file__).parent / "www" / "curbcharge-card.js"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                "/curbcharge/curbcharge-card.js",
                str(card_path),
                cache_headers=False,
            )
        ]
    )
    async_setup_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry[Any]) -> bool:
    """Normalize saved favorites while migrating config-entry options."""
    if entry.version == CONFIG_ENTRY_VERSION:
        return True
    if entry.version != 1:
        _LOGGER.error(
            "Cannot migrate CurbCharge config entry from version %s", entry.version
        )
        return False

    provider_id = entry.options.get(
        CONF_PROVIDER, entry.data.get(CONF_PROVIDER, PROVIDER_NDW_DOT_NL)
    )
    if not isinstance(provider_id, str):
        _LOGGER.error("Cannot migrate CurbCharge favorite references: invalid provider")
        return False
    try:
        favourites = parse_favourite_references(
            entry.options.get(CONF_FAVOURITES, []), provider_id
        )
    except ValueError:
        _LOGGER.error(
            "Cannot migrate CurbCharge favorite references: invalid saved data"
        )
        return False

    options = {
        **entry.options,
        CONF_FAVOURITES: [favourite.as_config() for favourite in favourites],
    }
    hass.config_entries.async_update_entry(
        entry,
        options=options,
        version=CONFIG_ENTRY_VERSION,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CurbChargeConfigEntry) -> bool:
    """Set up CurbCharge from a config entry."""
    location = _entry_location(hass, entry.data)
    if location is None:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="location_not_configured",
        )

    options = {**entry.data, **entry.options}
    provider_id = options.get(CONF_PROVIDER, PROVIDER_NDW_DOT_NL)
    radius_m = options.get(CONF_SEARCH_RADIUS, DEFAULT_SEARCH_RADIUS_M)
    max_stations = options.get(CONF_MAX_STATIONS, DEFAULT_MAX_STATIONS)
    scan_interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
    include_unknown = options.get(CONF_INCLUDE_UNKNOWN, DEFAULT_INCLUDE_UNKNOWN)
    location_movement_threshold_m = _location_movement_threshold(
        options, entry.data.get(CONF_LOCATION_SOURCE)
    )
    discovery_filters = DiscoveryFilterSettings()
    if provider_id == PROVIDER_NDW_DOT_NL:
        try:
            discovery_filters = DiscoveryFilterSettings.from_config(options)
        except ValueError as err:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="invalid_config",
            ) from err
    api_token = entry.data.get(CONF_API_TOKEN)
    try:
        favourites = parse_favourite_references(
            options.get(CONF_FAVOURITES, []), provider_id
        )
    except ValueError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="invalid_config",
        ) from err
    if (
        provider_id not in (PROVIDER_NDW_DOT_NL, PROVIDER_ECO_MOVEMENT_BE)
        or (
            provider_id == PROVIDER_ECO_MOVEMENT_BE
            and (not isinstance(api_token, str) or not api_token)
        )
        or not isinstance(radius_m, int)
        or not MIN_SEARCH_RADIUS_M <= radius_m <= MAX_SEARCH_RADIUS_M
        or not isinstance(max_stations, int)
        or not MIN_MAX_STATIONS <= max_stations <= MAX_MAX_STATIONS
        or not isinstance(scan_interval, int)
        or scan_interval < MIN_SCAN_INTERVAL_MINUTES
        or not isinstance(include_unknown, bool)
        or location_movement_threshold_m is None
    ):
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="invalid_config",
        )

    try:
        provider = create_provider(
            provider_id, async_get_clientsession(hass), hass, api_token
        )
    except ValueError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="invalid_config",
        ) from err
    except ChargingProviderError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="unsupported_provider",
        ) from err

    location_resolver: Callable[[], tuple[float, float] | None] | None = None
    if entry.data.get(CONF_LOCATION_SOURCE) == LOCATION_ENTITY:

        def resolve_entity_location() -> tuple[float, float] | None:
            return _entry_location(hass, entry.data)

        location_resolver = resolve_entity_location

    coordinator = CurbChargeCoordinator(
        hass,
        provider,
        location[0],
        location[1],
        radius_m,
        max_stations,
        scan_interval,
        include_unknown,
        favourites,
        config_entry=entry,
        location_resolver=location_resolver,
        location_movement_threshold_m=location_movement_threshold_m,
        discovery_filters=discovery_filters,
    )
    entry.runtime_data = coordinator
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady as err:
        cause = err.__cause__
        if err.translation_key == "rate_limited" or (
            isinstance(cause, UpdateFailed) and cause.translation_key == "rate_limited"
        ):
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="rate_limited",
            ) from err
        raise
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CurbChargeConfigEntry) -> bool:
    """Unload CurbCharge platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(
    hass: HomeAssistant, entry: CurbChargeConfigEntry
) -> None:
    coordinator = getattr(entry, "runtime_data", None)
    if not isinstance(coordinator, CurbChargeCoordinator):
        await hass.config_entries.async_reload(entry.entry_id)
        return

    if coordinator.entry_data_changed(entry.data):
        await hass.config_entries.async_reload(entry.entry_id)
        return

    options = {**entry.data, **entry.options}
    provider_id = options.get(CONF_PROVIDER, PROVIDER_NDW_DOT_NL)
    radius_m = options.get(CONF_SEARCH_RADIUS, DEFAULT_SEARCH_RADIUS_M)
    max_stations = options.get(CONF_MAX_STATIONS, DEFAULT_MAX_STATIONS)
    scan_interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
    include_unknown = options.get(CONF_INCLUDE_UNKNOWN, DEFAULT_INCLUDE_UNKNOWN)
    location_movement_threshold_m = _location_movement_threshold(
        options, entry.data.get(CONF_LOCATION_SOURCE)
    )
    try:
        discovery_filters = (
            DiscoveryFilterSettings.from_config(options)
            if provider_id == PROVIDER_NDW_DOT_NL
            else DiscoveryFilterSettings()
        )
    except ValueError:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    if (
        location_movement_threshold_m is None
        or provider_id != coordinator.provider_id
        or not isinstance(radius_m, int)
        or not MIN_SEARCH_RADIUS_M <= radius_m <= MAX_SEARCH_RADIUS_M
        or not isinstance(max_stations, int)
        or not MIN_MAX_STATIONS <= max_stations <= MAX_MAX_STATIONS
        or not isinstance(scan_interval, int)
        or scan_interval < MIN_SCAN_INTERVAL_MINUTES
        or not isinstance(include_unknown, bool)
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        return

    try:
        favourites = parse_favourite_references(
            options.get(CONF_FAVOURITES, []), provider_id
        )
    except ValueError:
        await hass.config_entries.async_reload(entry.entry_id)
        return

    favourites_changed = favourites != coordinator.favourites
    favourites_only_changed = favourites_changed and (
        radius_m == coordinator.radius_m
        and max_stations == coordinator.max_stations
        and scan_interval == coordinator.scan_interval_minutes
        and include_unknown == coordinator.include_unknown
        and location_movement_threshold_m == coordinator.location_movement_threshold_m
        and discovery_filters == coordinator.discovery_filters
    )
    coordinator.update_options(
        radius_m=radius_m,
        max_stations=max_stations,
        scan_interval_minutes=scan_interval,
        include_unknown=include_unknown,
        favourites=favourites,
        location_movement_threshold_m=location_movement_threshold_m,
        discovery_filters=discovery_filters,
    )
    if favourites_changed:
        coordinator.async_update_listeners()
    if favourites_only_changed:
        return
    await coordinator.async_request_refresh()


def _entry_location(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> tuple[float, float] | None:
    return resolve_location(hass, data)


def _location_movement_threshold(
    options: Mapping[str, Any], location_source: object
) -> int | None:
    if location_source != LOCATION_ENTITY:
        return DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M

    threshold = options.get(
        CONF_LOCATION_MOVEMENT_THRESHOLD, DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M
    )
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0:
        return None
    return threshold
