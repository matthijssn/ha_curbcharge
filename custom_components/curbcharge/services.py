"""Favorite management services for CurbCharge."""

from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.service import async_register_admin_service

from .const import CONF_FAVOURITES, DOMAIN
from .coordinator import CurbChargeCoordinator
from .models import FavouriteStationReference, parse_favourite_references
from .schema import vol

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_STATION_ID = "station_id"
SERVICE_ADD_FAVORITE = "add_favorite"
SERVICE_REMOVE_FAVORITE = "remove_favorite"

_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): selector.ConfigEntrySelector(
            selector.ConfigEntrySelectorConfig(integration=DOMAIN)
        ),
        vol.Required(ATTR_STATION_ID): cv.string,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register admin-only favorite management services."""

    async def handle_add_favorite(call: ServiceCall) -> None:
        await async_add_favorite(hass, call)

    async def handle_remove_favorite(call: ServiceCall) -> None:
        await async_remove_favorite(hass, call)

    async_register_admin_service(
        hass, DOMAIN, SERVICE_ADD_FAVORITE, handle_add_favorite, _SERVICE_SCHEMA
    )
    async_register_admin_service(
        hass, DOMAIN, SERVICE_REMOVE_FAVORITE, handle_remove_favorite, _SERVICE_SCHEMA
    )


async def async_add_favorite(hass: HomeAssistant, call: ServiceCall) -> None:
    """Save a fresh, discovered station as a favorite."""
    entry, coordinator = _get_entry_and_coordinator(hass, call)
    station_id = call.data[ATTR_STATION_ID]
    if not station_id.strip():
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_station_id"
        )
    favourites = _entry_favourites(entry, coordinator)
    if any(favourite.provider_id == station_id for favourite in favourites):
        return

    station = (coordinator.data or {}).get(station_id)
    if (
        not coordinator.last_update_success
        or coordinator.nearby_station_ids is None
        or station_id not in coordinator.nearby_station_ids
        or station is None
        or station.is_stale
        or station.provider != coordinator.provider_id
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="station_not_discovered"
        )

    _save_favourites(
        hass,
        entry,
        (*favourites, FavouriteStationReference.from_station(station)),
    )


async def async_remove_favorite(hass: HomeAssistant, call: ServiceCall) -> None:
    """Remove a saved favorite, including one no longer found upstream."""
    entry, coordinator = _get_entry_and_coordinator(hass, call)
    station_id = call.data[ATTR_STATION_ID]
    if not station_id.strip():
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_station_id"
        )
    favourites = _entry_favourites(entry, coordinator)
    if not any(favourite.provider_id == station_id for favourite in favourites):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="favorite_not_found"
        )

    _save_favourites(
        hass,
        entry,
        tuple(
            favourite for favourite in favourites if favourite.provider_id != station_id
        ),
    )


def _get_entry_and_coordinator(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[ConfigEntry[Any], CurbChargeCoordinator]:
    entry = hass.config_entries.async_get_entry(call.data[ATTR_CONFIG_ENTRY_ID])
    coordinator = getattr(entry, "runtime_data", None) if entry is not None else None
    if (
        entry is None
        or entry.domain != DOMAIN
        or entry.state is not ConfigEntryState.LOADED
        or not isinstance(coordinator, CurbChargeCoordinator)
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="config_entry_not_loaded"
        )
    return entry, coordinator


def _entry_favourites(
    entry: ConfigEntry[Any], coordinator: CurbChargeCoordinator
) -> tuple[FavouriteStationReference, ...]:
    try:
        return parse_favourite_references(
            entry.options.get(CONF_FAVOURITES, []), coordinator.provider_id
        )
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="invalid_config"
        ) from err


def _save_favourites(
    hass: HomeAssistant,
    entry: ConfigEntry[Any],
    favourites: tuple[FavouriteStationReference, ...],
) -> None:
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            CONF_FAVOURITES: [favourite.as_config() for favourite in favourites],
        },
    )
