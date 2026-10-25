"""Sanitized diagnostics for CurbCharge."""

import hashlib
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from . import CurbChargeConfigEntry
from .const import (
    CONF_API_TOKEN,
    CONF_FAVOURITES,
    CONF_INCLUDE_UNKNOWN,
    CONF_LOCATION_ENTITY,
    CONF_LOCATION_SOURCE,
    CONF_PERSONALIZATION,
    CONF_PROVIDER,
    CONF_SEARCH_RADIUS,
    MAX_MAX_STATIONS,
    MISSING_STATION_REFRESHES,
)
from .coordinator import CurbChargeCoordinator

TO_REDACT = [
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_LOCATION_ENTITY,
    CONF_API_TOKEN,
    CONF_FAVOURITES,
    CONF_PERSONALIZATION,
]


def _station_id_hash(entry_id: str, provider: str, station_id: str) -> str:
    return hashlib.sha256(f"{entry_id}:{provider}:{station_id}".encode()).hexdigest()[
        :16
    ]


def _favourite_presence(coordinator: CurbChargeCoordinator, station_id: str) -> str:
    if not coordinator.last_update_success:
        return "refresh_failed"
    station = (coordinator.data or {}).get(station_id)
    if station is None:
        return "not_discovered"
    if not station.is_stale:
        return "present"
    if coordinator.missing_refresh_count(station_id) >= MISSING_STATION_REFRESHES:
        return "missing"
    return "stale"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CurbChargeConfigEntry
) -> dict[str, Any]:
    """Return useful diagnostics without location or full address data."""
    coordinator: CurbChargeCoordinator = entry.runtime_data
    fresh_station_count = sum(
        not station.is_stale for station in (coordinator.data or {}).values()
    )
    current_stations = [
        station
        for station in (coordinator.data or {}).values()
        if not station.is_stale and station.provider_id not in coordinator.favourite_ids
    ]
    now = dt_util.utcnow()
    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT),
        "options": async_redact_data(entry.options, TO_REDACT),
        "provider": coordinator.provider_name,
        "provider_id": coordinator.provider_id,
        "country_code": coordinator.country_code,
        "configured_provider": entry.options.get(
            CONF_PROVIDER, entry.data.get(CONF_PROVIDER)
        ),
        "location_source": entry.data.get(CONF_LOCATION_SOURCE),
        "radius_m": entry.options.get(
            CONF_SEARCH_RADIUS, entry.data.get(CONF_SEARCH_RADIUS)
        ),
        "polling_interval_minutes": coordinator.scan_interval_minutes,
        "include_unknown": entry.options.get(
            CONF_INCLUDE_UNKNOWN, coordinator.include_unknown
        ),
        "favourite_count": len(coordinator.favourite_ids),
        "coordinator_update_success": coordinator.last_update_success,
        "favourite_references_truncated": (
            len(coordinator.favourites) > MAX_MAX_STATIONS
        ),
        "favourite_references": [
            {
                "provider": favourite.provider,
                "station_id_hash": _station_id_hash(
                    entry.entry_id, favourite.provider, favourite.provider_id
                ),
                "presence": _favourite_presence(coordinator, favourite.provider_id),
                "missing_refresh_count": coordinator.missing_refresh_count(
                    favourite.provider_id
                ),
            }
            for favourite in coordinator.favourites[:MAX_MAX_STATIONS]
        ],
        "discovered_stations": fresh_station_count,
        "retained_stations": len(coordinator.data or {}) - fresh_station_count,
        "last_successful_update": (
            coordinator.last_successful_update.isoformat()
            if coordinator.last_successful_update
            else None
        ),
        "stations": [
            {
                "provider_station_id_hash": _station_id_hash(
                    entry.entry_id, station.provider, station.provider_id
                ),
                "provider": station.provider,
                "provider_name": station.provider_name,
                "country_code": station.country_code,
                "operator": station.operator,
                "availability_reported": station.availability_reported,
                "connector_count_reported": station.connector_count_reported,
                "evse_status": (
                    station.evse_status.value
                    if station.evse_status is not None
                    else None
                ),
                "available_connectors": station.available_connectors,
                "unavailable_connectors": station.unavailable_connectors,
                "occupied_connectors": station.occupied_connectors,
                "reserved_connectors": station.reserved_connectors,
                "out_of_service_connectors": station.out_of_service_connectors,
                "unknown_connectors": station.unknown_connectors,
                "connector_types": list(station.connector_types),
                "max_power_kw": station.max_power_kw,
                "tariff": (
                    {
                        "currency": station.tariff.currency,
                        "price_per_kwh": (
                            str(station.tariff.price_per_kwh)
                            if station.tariff.price_per_kwh is not None
                            else None
                        ),
                        "session_fee": (
                            str(station.tariff.session_fee)
                            if station.tariff.session_fee is not None
                            else None
                        ),
                        "price_per_minute": (
                            str(station.tariff.price_per_minute)
                            if station.tariff.price_per_minute is not None
                            else None
                        ),
                    }
                    if station.tariff
                    else None
                ),
                "is_open": station.is_open,
                "last_updated": (
                    station.last_updated.isoformat() if station.last_updated else None
                ),
                "provider_data_age_seconds": (station.provider_data_age_seconds(now)),
                "provider_timestamp_source": (
                    station.last_updated_source.value
                    if station.last_updated_source is not None
                    else None
                ),
                "connectors": [
                    {
                        "type": connector.connector_type,
                        "status": connector.status.value,
                        "count": connector.count,
                        "available_count": connector.available_count,
                        "max_power_kw": connector.max_power_kw,
                    }
                    for connector in station.connectors
                ],
            }
            for station in current_stations
        ],
    }
