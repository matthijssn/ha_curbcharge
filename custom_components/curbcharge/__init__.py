"""CurbCharge Home Assistant integration."""

import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import UpdateFailed

# HA 2024.12 exports at http; current Core types it in http.server.
if TYPE_CHECKING:
    from homeassistant.components.http.server import StaticPathConfig
else:
    from homeassistant.components.http import StaticPathConfig

from .const import (
    CONF_API_TOKEN,
    CONF_DEVICE_TRACKERS,
    CONF_ENTRY_NAME,
    CONF_EV_ESTIMATES,
    CONF_FAVOURITES,
    CONF_INCLUDE_UNKNOWN,
    CONF_LOCATION_MOVEMENT_THRESHOLD,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_NOTIFY_ON_ERROR,
    CONF_PERSONALIZATION,
    CONF_PROVIDER,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH_RADIUS,
    CONFIG_ENTRY_VERSION,
    DEFAULT_DEVICE_TRACKERS,
    DEFAULT_INCLUDE_UNKNOWN,
    DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M,
    DEFAULT_MAX_STATIONS,
    DEFAULT_NOTIFY_ON_ERROR,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SEARCH_RADIUS_M,
    LOCATION_ENTITY,
    MAX_MAX_STATIONS,
    MAX_SEARCH_RADIUS_M,
    MIN_MAX_STATIONS,
    MIN_SCAN_INTERVAL_MINUTES,
    MIN_SEARCH_RADIUS_M,
    OPTIONAL_PLATFORMS,
    PLATFORMS,
    PROVIDER_NDW_DOT_NL,
    PROVIDERS_REQUIRING_API_TOKEN,
    SUPPORTED_PROVIDER_IDS,
)
from .const import (
    DOMAIN as DOMAIN,
)
from .coordinator import CurbChargeCoordinator
from .entry_title import entry_title, normalize_entry_name
from .location import resolve_location
from .models import (
    DiscoveryFilterSettings,
    EVEstimateSettings,
    PersonalizationProfile,
    parse_favourite_references,
)
from .notifications import (
    async_create_error_notification,
    async_dismiss_error_notifications,
)
from .providers import create_provider
from .providers.base import ChargingProviderError
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
    """Normalize saved favorites and initialize entry titles."""
    if entry.version == CONFIG_ENTRY_VERSION:
        return True
    if entry.version not in (1, 2):
        _LOGGER.error(
            "Cannot migrate CurbCharge config entry from version %s", entry.version
        )
        return False

    options = dict(entry.options)
    if entry.version == 1:
        provider_id = options.get(
            CONF_PROVIDER, entry.data.get(CONF_PROVIDER, PROVIDER_NDW_DOT_NL)
        )
        if not isinstance(provider_id, str):
            _LOGGER.error(
                "Cannot migrate CurbCharge favorite references: invalid provider"
            )
            return False
        try:
            favourites = parse_favourite_references(
                options.get(CONF_FAVOURITES, []), provider_id
            )
        except ValueError:
            _LOGGER.error(
                "Cannot migrate CurbCharge favorite references: invalid saved data"
            )
            return False
        options[CONF_FAVOURITES] = [favourite.as_config() for favourite in favourites]

    entry_name = normalize_entry_name(
        options.get(CONF_ENTRY_NAME, entry.data.get(CONF_ENTRY_NAME, ""))
    )
    if entry_name:
        options[CONF_ENTRY_NAME] = entry_name
        title = entry_name
    elif entry.title == "CurbCharge":
        title = entry_title(hass, {**entry.data, **options})
    else:
        title = entry.title
        options[CONF_ENTRY_NAME] = title

    hass.config_entries.async_update_entry(
        entry,
        options=options,
        title=title,
        version=CONFIG_ENTRY_VERSION,
    )
    return True


async def _async_notify_setup_error(
    hass: HomeAssistant, entry: CurbChargeConfigEntry, translation_key: str
) -> None:
    if entry.options.get(CONF_NOTIFY_ON_ERROR) is True:
        await async_create_error_notification(
            hass, entry.entry_id, "setup", translation_key
        )


async def async_setup_entry(hass: HomeAssistant, entry: CurbChargeConfigEntry) -> bool:
    """Set up CurbCharge from a config entry."""
    options = {**entry.data, **entry.options}
    entry_name = options.get(CONF_ENTRY_NAME, "")
    if not isinstance(entry_name, str):
        await _async_notify_setup_error(hass, entry, "invalid_config")
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="invalid_config",
        )
    title = entry_title(hass, options)
    if (
        entry.title != title
        and hass.config_entries.async_get_entry(entry.entry_id) is not None
    ):
        hass.config_entries.async_update_entry(entry, title=title)

    location = _entry_location(hass, entry.data)
    if location is None:
        await _async_notify_setup_error(hass, entry, "location_not_configured")
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="location_not_configured",
        )

    provider_id = options.get(CONF_PROVIDER, PROVIDER_NDW_DOT_NL)
    radius_m = options.get(CONF_SEARCH_RADIUS, DEFAULT_SEARCH_RADIUS_M)
    max_stations = options.get(CONF_MAX_STATIONS, DEFAULT_MAX_STATIONS)
    scan_interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
    include_unknown = options.get(CONF_INCLUDE_UNKNOWN, DEFAULT_INCLUDE_UNKNOWN)
    notify_on_error = options.get(CONF_NOTIFY_ON_ERROR, DEFAULT_NOTIFY_ON_ERROR)
    device_trackers = options.get(CONF_DEVICE_TRACKERS, DEFAULT_DEVICE_TRACKERS)
    location_movement_threshold_m = _location_movement_threshold(
        options, entry.data.get(CONF_LOCATION_SOURCE)
    )
    discovery_filters = DiscoveryFilterSettings()
    if provider_id == PROVIDER_NDW_DOT_NL:
        try:
            discovery_filters = DiscoveryFilterSettings.from_config(options)
        except ValueError as err:
            await _async_notify_setup_error(hass, entry, "invalid_config")
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="invalid_config",
            ) from err
    try:
        personalization_profile = PersonalizationProfile.from_config(
            options.get(CONF_PERSONALIZATION)
        )
        ev_estimate_settings = EVEstimateSettings.from_config(
            options.get(CONF_EV_ESTIMATES)
        )
    except ValueError as err:
        await _async_notify_setup_error(hass, entry, "invalid_config")
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
        await _async_notify_setup_error(hass, entry, "invalid_config")
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="invalid_config",
        ) from err
    if (
        provider_id not in SUPPORTED_PROVIDER_IDS
        or (
            provider_id in PROVIDERS_REQUIRING_API_TOKEN
            and (not isinstance(api_token, str) or not api_token)
        )
        or not isinstance(radius_m, int)
        or not MIN_SEARCH_RADIUS_M <= radius_m <= MAX_SEARCH_RADIUS_M
        or not isinstance(max_stations, int)
        or not MIN_MAX_STATIONS <= max_stations <= MAX_MAX_STATIONS
        or not isinstance(scan_interval, int)
        or scan_interval < MIN_SCAN_INTERVAL_MINUTES
        or not isinstance(include_unknown, bool)
        or not isinstance(notify_on_error, bool)
        or not isinstance(device_trackers, bool)
        or location_movement_threshold_m is None
    ):
        await _async_notify_setup_error(hass, entry, "invalid_config")
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="invalid_config",
        )

    try:
        provider = create_provider(
            provider_id, async_get_clientsession(hass), hass, api_token
        )
    except ValueError as err:
        await _async_notify_setup_error(hass, entry, "invalid_config")
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="invalid_config",
        ) from err
    except ChargingProviderError as err:
        await _async_notify_setup_error(hass, entry, "unsupported_provider")
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
        personalization_profile=personalization_profile,
        ev_estimate_settings=ev_estimate_settings,
        device_trackers_enabled=device_trackers,
        notify_on_error=notify_on_error,
    )
    entry.runtime_data = coordinator
    if provider.defers_initial_refresh:
        # The first refresh can take longer than Home Assistant allows for
        # setup during startup, which would leave the entry in a permanent
        # setup error. Entities report unavailable until the first data
        # arrives and are added as stations appear.
        coordinator.last_update_success = False
        entry.async_create_background_task(
            hass,
            coordinator.async_refresh(),
            f"{DOMAIN} initial refresh {entry.entry_id}",
        )
    else:
        try:
            await coordinator.async_config_entry_first_refresh()
        except ConfigEntryNotReady as err:
            cause = err.__cause__
            if err.translation_key == "rate_limited" or (
                isinstance(cause, UpdateFailed)
                and cause.translation_key == "rate_limited"
            ):
                raise ConfigEntryNotReady(
                    translation_domain=DOMAIN,
                    translation_key="rate_limited",
                ) from err
            raise
    platforms = PLATFORMS + OPTIONAL_PLATFORMS if device_trackers else PLATFORMS
    try:
        await hass.config_entries.async_forward_entry_setups(entry, platforms)
    except HomeAssistantError:
        await _async_notify_setup_error(hass, entry, "setup_failed")
        raise
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CurbChargeConfigEntry) -> bool:
    """Unload CurbCharge platforms."""
    coordinator = getattr(entry, "runtime_data", None)
    platforms = (
        PLATFORMS + OPTIONAL_PLATFORMS
        if isinstance(coordinator, CurbChargeCoordinator)
        and coordinator.device_trackers_enabled
        else PLATFORMS
    )
    return await hass.config_entries.async_unload_platforms(entry, platforms)


async def async_remove_entry(hass: HomeAssistant, entry: CurbChargeConfigEntry) -> None:
    """Dismiss notifications after a CurbCharge entry is removed."""
    async_dismiss_error_notifications(hass, entry.entry_id)


async def _async_options_updated(
    hass: HomeAssistant, entry: CurbChargeConfigEntry
) -> None:
    notify_on_error = entry.options.get(CONF_NOTIFY_ON_ERROR, DEFAULT_NOTIFY_ON_ERROR)
    if not isinstance(notify_on_error, bool):
        await hass.config_entries.async_reload(entry.entry_id)
        return
    if not notify_on_error:
        async_dismiss_error_notifications(hass, entry.entry_id)

    coordinator = getattr(entry, "runtime_data", None)
    if not isinstance(coordinator, CurbChargeCoordinator):
        await hass.config_entries.async_reload(entry.entry_id)
        return

    if coordinator.entry_data_changed(entry.data):
        await hass.config_entries.async_reload(entry.entry_id)
        return

    options = {**entry.data, **entry.options}
    entry_name = options.get(CONF_ENTRY_NAME, "")
    if not isinstance(entry_name, str):
        await hass.config_entries.async_reload(entry.entry_id)
        return
    provider_id = options.get(CONF_PROVIDER, PROVIDER_NDW_DOT_NL)
    radius_m = options.get(CONF_SEARCH_RADIUS, DEFAULT_SEARCH_RADIUS_M)
    max_stations = options.get(CONF_MAX_STATIONS, DEFAULT_MAX_STATIONS)
    scan_interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
    include_unknown = options.get(CONF_INCLUDE_UNKNOWN, DEFAULT_INCLUDE_UNKNOWN)
    device_trackers = options.get(CONF_DEVICE_TRACKERS, DEFAULT_DEVICE_TRACKERS)
    if not isinstance(device_trackers, bool):
        await hass.config_entries.async_reload(entry.entry_id)
        return
    device_trackers_changed = device_trackers != coordinator.device_trackers_enabled
    location_movement_threshold_m = _location_movement_threshold(
        options, entry.data.get(CONF_LOCATION_SOURCE)
    )
    try:
        discovery_filters = (
            DiscoveryFilterSettings.from_config(options)
            if provider_id == PROVIDER_NDW_DOT_NL
            else DiscoveryFilterSettings()
        )
        personalization_profile = PersonalizationProfile.from_config(
            options.get(CONF_PERSONALIZATION)
        )
        ev_estimate_settings = EVEstimateSettings.from_config(
            options.get(CONF_EV_ESTIMATES)
        )
    except ValueError:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    if ev_estimate_settings != coordinator.ev_estimate_settings:
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

    title = entry_title(hass, options)
    notify_on_error_changed = notify_on_error != coordinator.notify_on_error
    if notify_on_error_changed:
        coordinator.notify_on_error = notify_on_error
        if notify_on_error:
            await coordinator.async_notify_current_update_failure()

    if device_trackers_changed:
        try:
            if device_trackers:
                await hass.config_entries.async_forward_entry_setups(
                    entry, OPTIONAL_PLATFORMS
                )
            elif not await hass.config_entries.async_unload_platforms(
                entry, OPTIONAL_PLATFORMS
            ):
                _LOGGER.error(
                    "Could not unload CurbCharge map trackers; reloading config entry"
                )
                await hass.config_entries.async_reload(entry.entry_id)
                return
        except (HomeAssistantError, ImportError):
            _LOGGER.exception(
                "Could not update CurbCharge map trackers; reloading config entry"
            )
            await hass.config_entries.async_reload(entry.entry_id)
            return
        coordinator.device_trackers_enabled = device_trackers

    favourites_changed = favourites != coordinator.favourites
    personalization_changed = (
        personalization_profile != coordinator.personalization_profile
    )
    refresh_options_unchanged = (
        radius_m == coordinator.radius_m
        and max_stations == coordinator.max_stations
        and scan_interval == coordinator.scan_interval_minutes
        and include_unknown == coordinator.include_unknown
        and location_movement_threshold_m == coordinator.location_movement_threshold_m
        and discovery_filters == coordinator.discovery_filters
    )
    favourites_only_changed = (
        favourites_changed and refresh_options_unchanged and not personalization_changed
    )
    personalization_only_changed = (
        personalization_changed and refresh_options_unchanged and not favourites_changed
    )
    tracker_only_changed = (
        device_trackers_changed
        and refresh_options_unchanged
        and not favourites_changed
        and not personalization_changed
    )
    notification_only_changed = (
        notify_on_error_changed
        and refresh_options_unchanged
        and not favourites_changed
        and not personalization_changed
        and not device_trackers_changed
    )
    coordinator.update_options(
        radius_m=radius_m,
        max_stations=max_stations,
        scan_interval_minutes=scan_interval,
        include_unknown=include_unknown,
        favourites=favourites,
        location_movement_threshold_m=location_movement_threshold_m,
        discovery_filters=discovery_filters,
        personalization_profile=personalization_profile,
    )
    if entry.title != title:
        hass.config_entries.async_update_entry(entry, title=title)
    if favourites_changed:
        coordinator.async_update_listeners()
    if personalization_only_changed:
        coordinator.recompute_cached_recommendations()
        return
    if favourites_only_changed:
        return
    if tracker_only_changed:
        return
    if notification_only_changed:
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
