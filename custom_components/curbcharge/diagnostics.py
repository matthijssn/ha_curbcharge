"""Sanitized diagnostics for CurbCharge."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant

from . import CurbChargeConfigEntry
from .const import (
    CONF_INCLUDE_UNKNOWN,
    CONF_LOCATION_SOURCE,
    CONF_PROVIDER,
    CONF_SEARCH_RADIUS,
    PROVIDER_NAME,
)
from .coordinator import CurbChargeCoordinator

TO_REDACT = [CONF_LATITUDE, CONF_LONGITUDE]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CurbChargeConfigEntry
) -> dict[str, Any]:
    """Return useful diagnostics without location or full address data."""
    coordinator: CurbChargeCoordinator = entry.runtime_data
    current_stations = [
        station for station in (coordinator.data or {}).values() if not station.is_stale
    ]
    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT),
        "options": async_redact_data(entry.options, TO_REDACT),
        "provider": PROVIDER_NAME,
        "provider_id": entry.options.get(CONF_PROVIDER, entry.data.get(CONF_PROVIDER)),
        "location_source": entry.data.get(CONF_LOCATION_SOURCE),
        "radius_m": entry.options.get(
            CONF_SEARCH_RADIUS, entry.data.get(CONF_SEARCH_RADIUS)
        ),
        "polling_interval_minutes": coordinator.scan_interval_minutes,
        "include_unknown": entry.options.get(
            CONF_INCLUDE_UNKNOWN, coordinator.include_unknown
        ),
        "discovered_stations": len(current_stations),
        "retained_stations": len(coordinator.data or {}) - len(current_stations),
        "last_successful_update": (
            coordinator.last_successful_update.isoformat()
            if coordinator.last_successful_update
            else None
        ),
        "stations": [
            {
                "provider_station_id": station.provider_id,
                "operator": station.operator,
                "distance_m_rounded": (
                    round(station.distance_m / 1000) * 1000
                    if station.distance_m is not None
                    else None
                ),
                "availability_reported": station.availability_reported,
                "available_connectors": station.available_connectors,
                "unavailable_connectors": station.unavailable_connectors,
                "occupied_connectors": station.occupied_connectors,
                "reserved_connectors": station.reserved_connectors,
                "out_of_service_connectors": station.out_of_service_connectors,
                "unknown_connectors": station.unknown_connectors,
                "connector_types": list(station.connector_types),
                "is_open": station.is_open,
                "last_updated": (
                    station.last_updated.isoformat() if station.last_updated else None
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
