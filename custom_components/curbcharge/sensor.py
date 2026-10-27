"""Sensors for CurbCharge stations and recommendations."""

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfLength, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    EntityPlatform,
    async_get_current_platform,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import MISSING_STATION_REFRESHES, SUPPORTED_COUNTRY_CODES
from .coordinator import (
    CurbChargeCoordinator,
    RecommendationResult,
    RecommendationResults,
)
from .entity import (
    CurbChargeStationEntity,
    get_or_create_location_device,
    migrate_entity_unique_id,
    setup_dynamic_station_entities,
)
from .models import ChargingConnector, ChargingStation, ChargingTariff, TariffStatus
from .statistics import availability_statistics

PARALLEL_UPDATES = 0
_TOP_NEARBY_LIMIT = 5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[CurbChargeCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up station and integration-level sensors."""
    coordinator = entry.runtime_data
    setup_dynamic_station_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        lambda station: create_station_sensors(coordinator, station),
        lambda station, existing_unique_ids: create_missing_connector_group_sensors(
            coordinator, station, existing_unique_ids
        ),
    )
    await async_setup_dynamic_favorite_status_sensors(
        hass, entry, coordinator, async_add_entities
    )
    aggregate_entities = [
        BestNearbyChargerSensor(coordinator, entry),
        TopNearbyChargersSensor(coordinator, entry),
        NearestAvailableChargerSensor(coordinator, entry),
        CheapestAvailableChargerSensor(coordinator, entry),
        FastestAvailableChargerSensor(coordinator, entry),
        NearbyChargingUnitsSensor(coordinator, entry),
        NearbyAvailableConnectorsSensor(coordinator, entry),
        NearbyAvailabilityPercentageSensor(coordinator, entry),
        FavoriteSummarySensor(coordinator, entry),
    ]
    _migrate_aggregate_entity_ids(hass, entry, aggregate_entities)
    async_add_entities(aggregate_entities)


def _migrate_aggregate_entity_ids(
    hass: HomeAssistant,
    entry: ConfigEntry[Any],
    entities: Sequence[Entity],
) -> None:
    """Keep aggregate entity IDs when the mutable search identity changes."""
    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    entities_by_suffix: dict[str, str] = {}
    for entity in entities:
        suffix = getattr(entity, "_unique_id_suffix", None)
        if isinstance(suffix, str) and entity.unique_id is not None:
            entities_by_suffix[suffix] = entity.unique_id
    for registry_entry in entries:
        if registry_entry.domain != "sensor":
            continue
        suffix = next(
            (
                suffix
                for suffix in entities_by_suffix
                if registry_entry.unique_id.endswith(f"_{suffix}")
            ),
            None,
        )
        if suffix is not None:
            migrate_entity_unique_id(
                hass,
                entry,
                "sensor",
                registry_entry.unique_id,
                entities_by_suffix[suffix],
            )


class AvailableConnectorsSensor(CurbChargeStationEntity, SensorEntity):
    """Number of connectors the provider reports as available."""

    _attr_translation_key = "available_connectors"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.available_connectors


class AvailabilityPercentageSensor(CurbChargeStationEntity, SensorEntity):
    """Expose the station's current binary availability as a percentage."""

    _attr_translation_key = "availability_percentage"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        station = self.station
        if station is None:
            return None
        available = station.has_available_connectors
        if available is None:
            return None
        return 100.0 if available else 0.0


class OccupiedConnectorsSensor(CurbChargeStationEntity, SensorEntity):
    """Occupied count only when the provider makes it knowable."""

    _attr_translation_key = "occupied_connectors"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.occupied_connectors


class UnavailableConnectorsSensor(CurbChargeStationEntity, SensorEntity):
    """Number of connectors not reported as available."""

    _attr_translation_key = "unavailable_connectors"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.unavailable_connectors


class TotalConnectorsSensor(CurbChargeStationEntity, SensorEntity):
    """Total number of connectors in the provider's availability report."""

    _attr_translation_key = "total_connectors"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.total_connectors


class ConnectorGroupSensor(CurbChargeStationEntity, SensorEntity):
    """Expose one provider-reported group of equivalent connectors."""

    _attr_translation_key = "connector_group"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: CurbChargeCoordinator,
        station: ChargingStation,
        connector: ChargingConnector,
    ) -> None:
        """Initialize a connector group without implying individual plug IDs."""
        group_index = connector.provider_connector_id.rsplit(":", 1)[-1]
        super().__init__(
            coordinator, station, unique_id_suffix=f"connector_group_{group_index}"
        )
        self._connector_id = connector.provider_connector_id
        self._attr_translation_placeholders = {
            "connector_type": _display_connector_type(connector.connector_type)
        }

    @property
    def connector(self) -> ChargingConnector | None:
        """Return the latest version of this provider group."""
        station = self.station
        if station is None:
            return None
        return next(
            (
                connector
                for connector in station.connectors
                if connector.provider_connector_id == self._connector_id
            ),
            None,
        )

    @property
    def available(self) -> bool:
        """Do not present a missing group as current station data."""
        return super().available and self.connector is not None

    @property
    def native_value(self) -> int | None:
        """Return the provider-reported total in this connector group."""
        connector = self.connector
        return None if connector is None else connector.count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose only counts and properties actually present in the feed."""
        connector = self.connector
        if connector is None:
            return {
                "available_connectors": None,
                "unavailable_connectors": None,
                "occupied_connectors": None,
                "connector_type": None,
                "connector_format": None,
                "power_type": None,
                "max_power_kw": None,
            }
        available = connector.available_count
        return {
            "available_connectors": available,
            "unavailable_connectors": (
                connector.count - available if available is not None else None
            ),
            "occupied_connectors": (0 if available == connector.count else None),
            "connector_type": connector.connector_type,
            "connector_format": connector.connector_format,
            "power_type": connector.power_type,
            "max_power_kw": connector.max_power_kw,
        }


def _display_connector_type(connector_type: str | None) -> str:
    if connector_type is None:
        return "Unknown"
    return connector_type.replace("_", " ").title()


class MaximumChargingPowerSensor(CurbChargeStationEntity, SensorEntity):
    """Highest reported charging power."""

    _attr_translation_key = "maximum_charging_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        station = self.station
        return None if station is None else station.max_power_kw


class PricePerKwhSensor(CurbChargeStationEntity, SensorEntity):
    """Provider-reported per-kWh price when unambiguous."""

    _attr_translation_key = "price_per_kwh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_unit_of_measurement(self) -> str | None:
        station = self.station
        if station is None or station.tariff is None:
            return None
        return f"{station.tariff.currency}/kWh"

    @property
    def native_value(self) -> float | None:
        station = self.station
        if (
            station is None
            or station.tariff is None
            or station.tariff.price_per_kwh is None
        ):
            return None
        return float(station.tariff.price_per_kwh)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station = self.station
        return {} if station is None else _tariff_attributes(station)


class DistanceSensor(CurbChargeStationEntity, SensorEntity):
    """Distance to the configured search location."""

    _attr_translation_key = "distance"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.distance_m


class StatusSummarySensor(CurbChargeStationEntity, SensorEntity):
    """Localized station availability summary and status attributes."""

    _attr_translation_key = "status_summary"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["available", "none_available", "unknown"]

    @property
    def native_value(self) -> str:
        station = self.station
        if station is None or station.has_available_connectors is None:
            return "unknown"
        return "available" if station.has_available_connectors else "none_available"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station = self.station
        if station is None:
            return {
                "provider_station_id": self._station_id,
                "provider_data_age_seconds": None,
                "provider_timestamp_source": None,
                **_best_nearby_rank_attributes(self.coordinator, None),
            }
        attributes = {
            "station_name": station.name,
            "available_connectors": station.available_connectors,
            "occupied_connectors": station.occupied_connectors,
            "unavailable_connectors": station.unavailable_connectors,
            "total_connectors": station.total_connectors,
            "reserved_connectors": station.reserved_connectors,
            "out_of_service_connectors": station.out_of_service_connectors,
            "unknown_connectors": station.unknown_connectors,
            "connector_types": list(station.connector_types),
            "operator": station.operator,
            "address": station.address,
            "distance_m": station.distance_m,
            "max_power_kw": station.max_power_kw,
            **_tariff_attributes(station),
            "latitude": station.latitude,
            "longitude": station.longitude,
            "provider_data_age_seconds": station.provider_data_age_seconds(
                dt_util.utcnow()
            ),
            "provider_timestamp_source": (
                station.last_updated_source.value
                if station.last_updated_source is not None
                else None
            ),
            "provider": station.provider,
            "country_code": station.country_code,
            "provider_station_id": station.provider_id,
            "is_favorite": station.provider_id in self.coordinator.favourite_ids,
            "last_updated": _isoformat(station.last_updated),
            "last_successful_update": _isoformat(
                self.coordinator.last_successful_update
            ),
            "is_open": station.is_open,
            "availability_reported": station.availability_reported,
            "evse_status": (
                station.evse_status.value if station.evse_status is not None else None
            ),
            **_best_nearby_rank_attributes(self.coordinator, station),
        }
        if station.tariff is None and any(
            connector.tariff_ids for connector in station.connectors
        ):
            attributes["connector_tariffs"] = [
                _connector_tariff_attributes(connector)
                for connector in station.connectors
            ]
        return attributes


class FavoriteStatusSensor(CurbChargeStationEntity, SensorEntity):
    """Expose one configured favorite's current, entry-scoped status."""

    _attr_translation_key = "favorite_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["available", "none_available", "unknown"]

    def __init__(
        self,
        coordinator: CurbChargeCoordinator,
        station: ChargingStation,
        entry: ConfigEntry[Any],
    ) -> None:
        super().__init__(
            coordinator, station, unique_id_suffix=f"{entry.entry_id}_favorite_status"
        )

    @property
    def native_value(self) -> str:
        station = self.station
        if station is None or station.available_connectors is None:
            return "unknown"
        return "available" if station.available_connectors > 0 else "none_available"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station = self.station
        attributes: dict[str, Any] = {
            "is_favorite": self._station_id in self.coordinator.favourite_ids,
            "provider_station_id": self._station_id,
            "provider_update_success": self.coordinator.last_update_success,
            "last_successful_update": _isoformat(
                self.coordinator.last_successful_update
            ),
            "missing_refresh_count": self.coordinator.missing_refresh_count(
                self._station_id
            ),
        }
        if station is None:
            attributes.update(
                {
                    "available_connectors": None,
                    "total_connectors": None,
                    "max_power_kw": None,
                    "price_per_kwh": None,
                    "currency": None,
                    "is_stale": None,
                    "provider_data_age_seconds": None,
                    "provider_timestamp_source": None,
                }
            )
            return attributes

        tariff_attributes = _tariff_attributes(station)
        attributes.update(
            {
                "available_connectors": station.available_connectors,
                "total_connectors": station.total_connectors,
                "max_power_kw": station.max_power_kw,
                "price_per_kwh": tariff_attributes["price_per_kwh"],
                "currency": tariff_attributes["currency"],
                "is_stale": station.is_stale,
                "provider_data_age_seconds": station.provider_data_age_seconds(
                    dt_util.utcnow()
                ),
                "provider_timestamp_source": (
                    station.last_updated_source.value
                    if station.last_updated_source is not None
                    else None
                ),
            }
        )
        return attributes


def _tariff_attributes(station: ChargingStation) -> dict[str, Any]:
    """Return the station-level CPO tariff in a stable sensor attribute shape."""
    tariff = station.tariff
    tariff_ids = {
        tariff_id
        for connector in station.connectors
        for tariff_id in connector.tariff_ids
    }
    if tariff is not None and tariff.tariff_id is not None:
        tariff_ids.add(tariff.tariff_id)
    return {
        "price_per_kwh": _decimal_attribute(
            tariff.price_per_kwh if tariff is not None else None
        ),
        "currency": tariff.currency if tariff is not None else None,
        "tariff_description": tariff.description if tariff is not None else None,
        "tariff_source": tariff.source if tariff is not None else None,
        "tariff_vat_percentage": _decimal_attribute(
            tariff.vat_percentage if tariff is not None else None
        ),
        "session_fee": _decimal_attribute(
            tariff.session_fee if tariff is not None else None
        ),
        "price_per_minute": _decimal_attribute(
            tariff.price_per_minute if tariff is not None else None
        ),
        "tariff_ids": sorted(tariff_ids),
        "tariff_type": tariff.tariff_type if tariff is not None else None,
        "tariff_status": _station_tariff_status(station).value,
    }


def _station_tariff_status(station: ChargingStation) -> TariffStatus:
    if station.tariff_status is not None:
        return station.tariff_status
    if station.tariff is not None:
        return station.tariff.effective_status
    if any(connector.tariff_ids for connector in station.connectors):
        return TariffStatus.INCOMPLETE
    return TariffStatus.NOT_REPORTED


def _connector_tariff_attributes(connector: ChargingConnector) -> dict[str, Any]:
    status = connector.tariff_status or (
        TariffStatus.INCOMPLETE if connector.tariff_ids else TariffStatus.NOT_REPORTED
    )
    return {
        "connector_type": connector.connector_type,
        "connector_format": connector.connector_format,
        "power_type": connector.power_type,
        "max_power_kw": connector.max_power_kw,
        "count": connector.count,
        "tariff_ids": list(connector.tariff_ids),
        "tariff_status": status.value,
        "tariffs": [
            {
                **_tariff_record_attributes(tariff),
                "tariff_id": tariff.tariff_id,
            }
            for tariff in connector.tariffs
        ],
    }


def _tariff_record_attributes(tariff: ChargingTariff) -> dict[str, Any]:
    return {
        "price_per_kwh": _decimal_attribute(tariff.price_per_kwh),
        "currency": tariff.currency,
        "tariff_description": tariff.description,
        "tariff_source": tariff.source,
        "tariff_vat_percentage": _decimal_attribute(tariff.vat_percentage),
        "session_fee": _decimal_attribute(tariff.session_fee),
        "price_per_minute": _decimal_attribute(tariff.price_per_minute),
        "tariff_type": tariff.tariff_type,
        "tariff_status": tariff.effective_status.value,
    }


def _decimal_attribute(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _current_stations(
    coordinator: CurbChargeCoordinator,
) -> tuple[ChargingStation, ...] | None:
    """Return current records within this entry's radius."""
    stations = coordinator.data
    if stations is None:
        return None
    return tuple(
        station
        for station in stations.values()
        if not station.is_stale
        and station.distance_m is not None
        and station.distance_m <= coordinator.radius_m
    )


def _best_nearby_result_status(
    coordinator: CurbChargeCoordinator,
    selected_station: ChargingStation | None,
) -> str:
    """Describe why Best nearby has or does not have a current result."""
    if not coordinator.last_update_success:
        return "unavailable"
    if selected_station is not None:
        return "selected"

    current_stations = _current_stations(coordinator)
    if not current_stations:
        return "no_current_stations"
    if any(station.has_available_connectors is None for station in current_stations):
        return "unknown_availability"
    return "none_available"


def _selection_result_status(
    coordinator: CurbChargeCoordinator,
    recommendation: RecommendationResult,
) -> str:
    """Describe why an alternative charger selection has or lacks a result."""
    if not coordinator.last_update_success or recommendation.reason == "unavailable":
        return "unavailable"
    if recommendation.station is not None:
        return "selected"
    if recommendation.reason in ("no_known_price", "mixed_currencies"):
        return "no_comparable_price"
    if recommendation.reason in ("no_positive_power", "no_known_distance"):
        return recommendation.reason

    current_stations = _current_stations(coordinator)
    if not current_stations:
        return "no_current_stations"
    if any(
        station.has_available_connectors is None or station.available_connectors is None
        for station in current_stations
    ):
        return "unknown_availability"
    return "none_available"


def _nearby_aggregate_attributes(
    coordinator: CurbChargeCoordinator,
    current_stations: tuple[ChargingStation, ...] | None,
) -> dict[str, Any]:
    """Return search scope and freshness attributes shared by aggregate sensors."""
    in_radius_stations = tuple(
        station
        for station in (coordinator.data or {}).values()
        if station.distance_m is not None and station.distance_m <= coordinator.radius_m
    )
    stale_station_count = (
        None
        if current_stations is None
        else sum(station.is_stale for station in in_radius_stations)
    )
    normal_station_ids = coordinator.nearby_station_ids
    if normal_station_ids is None:
        normal_station_ids = {
            station.provider_id
            for station in current_stations or ()
            if station.provider_id not in coordinator.favourite_ids
        }
    if current_stations is None:
        results_may_be_truncated = None
    elif (
        sum(station.provider_id in normal_station_ids for station in current_stations)
        >= coordinator.max_stations
    ):
        results_may_be_truncated = True
    elif stale_station_count == 0 and coordinator.include_unknown:
        results_may_be_truncated = False
    else:
        results_may_be_truncated = None

    return {
        "provider": coordinator.provider_name,
        "provider_id": coordinator.provider_id,
        "country_code": coordinator.country_code,
        "supported_country_codes": list(SUPPORTED_COUNTRY_CODES),
        "coverage_scope": "configured_search_area",
        "search_radius_m": coordinator.radius_m,
        "max_stations": coordinator.max_stations,
        "include_unknown": coordinator.include_unknown,
        "stale_station_count": stale_station_count,
        "results_may_be_truncated": results_may_be_truncated,
        "last_successful_update": _isoformat(coordinator.last_successful_update),
    }


def _favorite_summary_attributes(
    coordinator: CurbChargeCoordinator,
) -> dict[str, Any]:
    """Summarize configured favorite freshness and comparable recommendations."""
    configured_favorites = coordinator.favourites
    station_data = coordinator.data
    attributes: dict[str, Any] = {
        "configured_count": len(configured_favorites),
        "provider_update_success": coordinator.last_update_success,
        "last_successful_update": _isoformat(coordinator.last_successful_update),
    }

    if coordinator.last_update_success and station_data is not None:
        favorite_records = tuple(
            station_data.get(favourite.provider_id)
            for favourite in configured_favorites
        )
        favorite_stations = tuple(
            station
            for station in favorite_records
            if station is not None and not station.is_stale
        )
        attributes.update(
            {
                "discovered_count": len(favorite_stations),
                "known_count": sum(
                    station.available_connectors is not None
                    for station in favorite_stations
                ),
                "available_count": sum(
                    station.available_connectors is not None
                    and station.available_connectors > 0
                    for station in favorite_stations
                ),
                "unknown_count": sum(
                    station.available_connectors is None
                    for station in favorite_stations
                ),
                "stale_count": sum(
                    station is None or station.is_stale for station in favorite_records
                ),
                "missing_count": sum(
                    coordinator.missing_refresh_count(favourite.provider_id)
                    >= MISSING_STATION_REFRESHES
                    for favourite in configured_favorites
                ),
            }
        )
    else:
        favorite_stations = ()
        attributes.update(
            {
                "discovered_count": None,
                "known_count": None,
                "available_count": None,
                "unknown_count": None,
                "stale_count": None,
                "missing_count": None,
            }
        )

    results = (
        coordinator.recommendation_results if coordinator.last_update_success else None
    )
    unavailable = RecommendationResult(None, "unavailable", None, None, None)
    favorite_recommendations = (
        results.favorite_cheapest if results is not None else unavailable,
        results.favorite_nearest if results is not None else unavailable,
        results.favorite_fastest if results is not None else unavailable,
    )
    for key, recommendation in zip(
        (
            "cheapest_available_favorite",
            "nearest_available_favorite",
            "highest_power_available_favorite",
        ),
        favorite_recommendations,
        strict=True,
    ):
        attributes[key] = _station_selection_attributes(coordinator, recommendation)
    return attributes


class FavoriteSummarySensor(CoordinatorEntity[CurbChargeCoordinator], SensorEntity):
    """Summarize favorite discovery, availability, freshness, and recommendations."""

    _attr_has_entity_name = True
    _attr_translation_key = "favorite_summary"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: CurbChargeCoordinator, entry: ConfigEntry[Any]
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_favorite_summary"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.favourites)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return _favorite_summary_attributes(self.coordinator)


class NearbyChargingUnitsSensor(CoordinatorEntity[CurbChargeCoordinator], SensorEntity):
    """Count current provider charging units in the configured search area."""

    _attr_has_entity_name = True
    _attr_translation_key = "nearby_charging_units"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: CurbChargeCoordinator, entry: ConfigEntry[Any]
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_nearby_charging_units"
        self._unique_id_suffix = "nearby_charging_units"

    @property
    def native_value(self) -> int | None:
        current_stations = _current_stations(self.coordinator)
        if current_stations is None:
            return None
        return len(current_stations)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current_stations = _current_stations(self.coordinator)
        attributes = _nearby_aggregate_attributes(self.coordinator, current_stations)
        attributes["counted_unit"] = "charging_station"
        attributes["unknown_availability_count"] = (
            None
            if current_stations is None
            else sum(
                station.has_available_connectors is None for station in current_stations
            )
        )
        return attributes


class NearbyAvailableConnectorsSensor(
    CoordinatorEntity[CurbChargeCoordinator], SensorEntity
):
    """Count explicitly reported available connectors at current nearby stations."""

    _attr_has_entity_name = True
    _attr_translation_key = "nearby_available_connectors"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: CurbChargeCoordinator, entry: ConfigEntry[Any]
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_nearby_available_connectors"
        self._unique_id_suffix = "nearby_available_connectors"

    @property
    def native_value(self) -> int | None:
        current_stations = _current_stations(self.coordinator)
        if current_stations is None:
            return None

        available_counts = []
        for station in current_stations:
            count = station.available_connectors
            if count is not None:
                available_counts.append(count)
        return sum(available_counts) if available_counts else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current_stations = _current_stations(self.coordinator)
        attributes = _nearby_aggregate_attributes(self.coordinator, current_stations)
        attributes.update(
            {
                "counted_unit": "connector",
                "fresh_station_count": (
                    None if current_stations is None else len(current_stations)
                ),
                "known_availability_station_count": (
                    None
                    if current_stations is None
                    else sum(
                        station.available_connectors is not None
                        for station in current_stations
                    )
                ),
                "unknown_availability_station_count": (
                    None
                    if current_stations is None
                    else sum(
                        station.available_connectors is None
                        for station in current_stations
                    )
                ),
            }
        )
        return attributes


class NearbyAvailabilityPercentageSensor(
    CoordinatorEntity[CurbChargeCoordinator], SensorEntity
):
    """Report the weighted share of fresh connectors known to be available."""

    _attr_has_entity_name = True
    _attr_translation_key = "nearby_availability_percentage"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: CurbChargeCoordinator, entry: ConfigEntry[Any]
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_nearby_availability_percentage"
        self._unique_id_suffix = "nearby_availability_percentage"

    @property
    def native_value(self) -> float | None:
        summary = availability_statistics(_current_stations(self.coordinator))
        if summary.available_connectors is None or not summary.total_connectors:
            return None
        return round(100 * summary.available_connectors / summary.total_connectors, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current_stations = _current_stations(self.coordinator)
        summary = availability_statistics(current_stations)
        coverage = (
            None
            if not summary.reported_total_connectors or summary.total_connectors is None
            else round(
                100 * summary.total_connectors / summary.reported_total_connectors, 1
            )
        )
        unknown_availability_connectors = (
            None
            if summary.total_connectors is None
            or summary.reported_total_connectors is None
            else summary.reported_total_connectors - summary.total_connectors
        )
        attributes = _nearby_aggregate_attributes(self.coordinator, current_stations)
        attributes.update(
            {
                "counted_unit": "connector",
                "available_connectors": summary.available_connectors,
                "total_connectors": summary.total_connectors,
                "reported_total_connectors": summary.reported_total_connectors,
                "availability_coverage_percent": coverage,
                "unknown_availability_connector_count": (
                    unknown_availability_connectors
                ),
                "known_connector_group_count": summary.known_connector_group_count,
                "excluded_connector_group_count": (
                    summary.excluded_connector_group_count
                ),
                "unknown_availability_station_count": (
                    summary.unknown_availability_station_count
                ),
                "unreported_total_station_count": (
                    summary.unreported_total_station_count
                ),
                "fresh_station_count": (
                    None if current_stations is None else len(current_stations)
                ),
            }
        )
        return attributes


class BestNearbyChargerSensor(CoordinatorEntity[CurbChargeCoordinator], SensorEntity):
    """The best currently available station under the configured ranking."""

    _attr_has_entity_name = True
    _attr_translation_key = "best_nearby_charger"

    def __init__(
        self, coordinator: CurbChargeCoordinator, entry: ConfigEntry[Any]
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_best_nearby_charger"
        self._unique_id_suffix = "best_nearby_charger"

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.last_update_success:
            return None
        station = self.coordinator.best_station
        return None if station is None else station.name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        coordinator = self.coordinator
        station = coordinator.best_station if coordinator.last_update_success else None
        last_successful_update = _isoformat(coordinator.last_successful_update)
        attributes: dict[str, Any] = {
            "result_status": _best_nearby_result_status(coordinator, station),
            "reason": (
                coordinator.best_station_reason
                if station is not None
                else "no_available_station"
            ),
            "last_updated": last_successful_update,
            "last_successful_update": last_successful_update,
            "provider_data_age_seconds": None,
            "provider_timestamp_source": None,
            **_best_nearby_rank_attributes(
                coordinator,
                station,
            ),
        }
        if station is None:
            return attributes
        tariff = station.tariff
        attributes.update(_selection_station_data_attributes(station))
        attributes["is_favorite"] = station.provider_id in coordinator.favourite_ids
        attributes["provider_data_age_seconds"] = station.provider_data_age_seconds(
            dt_util.utcnow()
        )
        attributes["provider_timestamp_source"] = (
            station.last_updated_source.value
            if station.last_updated_source is not None
            else None
        )
        attributes["vat_percentage"] = (
            float(tariff.vat_percentage)
            if tariff is not None and tariff.vat_percentage is not None
            else None
        )
        return attributes


class TopNearbyChargersSensor(CoordinatorEntity[CurbChargeCoordinator], SensorEntity):
    """Count and list the five highest-ranked currently available stations."""

    _attr_has_entity_name = True
    _attr_translation_key = "top_nearby_chargers"

    def __init__(
        self, coordinator: CurbChargeCoordinator, entry: ConfigEntry[Any]
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_top_nearby_chargers"
        self._unique_id_suffix = "top_nearby_chargers"

    @property
    def native_value(self) -> int | None:
        recommendations = _top_nearby_recommendations(self.coordinator)
        return None if recommendations is None else len(recommendations)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        coordinator = self.coordinator
        results = (
            coordinator.recommendation_results
            if coordinator.last_update_success
            else None
        )
        best = results.best_nearby if results is not None else None
        last_successful_update = _isoformat(coordinator.last_successful_update)
        reason = (
            best.reason
            if best is not None and best.reason is not None
            else "no_available_station"
            if coordinator.last_update_success
            else "unavailable"
        )
        return {
            "result_status": _best_nearby_result_status(
                coordinator, best.station if best is not None else None
            ),
            "reason": reason,
            "last_updated": last_successful_update,
            "last_successful_update": last_successful_update,
            "best_nearby_candidate_count": (
                best.best_nearby_candidate_count if best is not None else None
            ),
            "recommendations": _top_nearby_recommendations(coordinator),
        }


class _SelectedChargerSensor(CoordinatorEntity[CurbChargeCoordinator], SensorEntity):
    """Base for deterministic integration-level charger selections."""

    _attr_has_entity_name = True
    _selector: Callable[[RecommendationResults], RecommendationResult]
    _unique_id_suffix: str

    def __init__(
        self,
        coordinator: CurbChargeCoordinator,
        entry: ConfigEntry[Any],
        selector: Callable[[RecommendationResults], RecommendationResult],
    ) -> None:
        super().__init__(coordinator)
        self._selector = selector
        self._attr_unique_id = f"{entry.entry_id}_{self._unique_id_suffix}"

    def _selection(self) -> RecommendationResult:
        results = self.coordinator.recommendation_results
        if not self.coordinator.last_update_success or results is None:
            return RecommendationResult(None, "unavailable", None, None, None)
        return self._selector(results)

    @property
    def native_value(self) -> str | None:
        station = self._selection().station
        return None if station is None else station.name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        recommendation = self._selection()
        return {
            **_station_selection_attributes(self.coordinator, recommendation),
            "result_status": _selection_result_status(self.coordinator, recommendation),
        }


class NearestAvailableChargerSensor(_SelectedChargerSensor):
    """The nearest fresh station with known available connectors."""

    _attr_translation_key = "nearest_available_charger"
    _unique_id_suffix = "nearest_available_charger"

    def __init__(self, coordinator: CurbChargeCoordinator, entry: ConfigEntry[Any]):
        super().__init__(coordinator, entry, lambda results: results.nearest_available)


class CheapestAvailableChargerSensor(_SelectedChargerSensor):
    """The cheapest fresh station with a comparable station-level price."""

    _attr_translation_key = "cheapest_available_charger"
    _unique_id_suffix = "cheapest_available_charger"

    def __init__(self, coordinator: CurbChargeCoordinator, entry: ConfigEntry[Any]):
        super().__init__(coordinator, entry, lambda results: results.cheapest_available)


class FastestAvailableChargerSensor(_SelectedChargerSensor):
    """The fresh available station with the highest reported power."""

    _attr_translation_key = "fastest_available_charger"
    _unique_id_suffix = "fastest_available_charger"

    def __init__(self, coordinator: CurbChargeCoordinator, entry: ConfigEntry[Any]):
        super().__init__(coordinator, entry, lambda results: results.fastest_available)


def _station_selection_attributes(
    coordinator: CurbChargeCoordinator,
    recommendation: RecommendationResult,
) -> dict[str, Any]:
    """Build the common result shape for deterministic selection sensors."""
    station = recommendation.station
    attributes: dict[str, Any] = {
        "reason": recommendation.reason or "no_available_station",
        "last_updated": _isoformat(coordinator.last_successful_update),
        "last_successful_update": _isoformat(coordinator.last_successful_update),
        "best_nearby_rank": recommendation.best_nearby_rank,
        "best_nearby_rank_score": recommendation.best_nearby_rank_score,
        "best_nearby_candidate_count": (recommendation.best_nearby_candidate_count),
    }
    if station is None:
        return attributes

    attributes.update(_selection_station_data_attributes(station))
    attributes["is_favorite"] = station.provider_id in coordinator.favourite_ids
    return attributes


def _top_nearby_recommendations(
    coordinator: CurbChargeCoordinator,
) -> list[dict[str, Any]] | None:
    """Build a bounded list from the coordinator's cached Best nearby ranks."""
    results = (
        coordinator.recommendation_results if coordinator.last_update_success else None
    )
    if results is None:
        return None

    ranked: list[tuple[int, float, int, ChargingStation]] = []
    for station in _current_stations(coordinator) or ():
        rank, score, candidate_count = coordinator.best_nearby_rank_for(station)
        if rank is not None and score is not None and candidate_count is not None:
            ranked.append((rank, score, candidate_count, station))
    ranked.sort(key=lambda recommendation: recommendation[0])

    recommendations: list[dict[str, Any]] = []
    for rank, score, candidate_count, station in ranked[:_TOP_NEARBY_LIMIT]:
        payload = _station_selection_attributes(
            coordinator,
            RecommendationResult(
                station, "best_nearby_rank", rank, score, candidate_count
            ),
        )
        payload.update(
            {
                "rank": rank,
                "score": score,
                "score_components": {
                    key: payload[key]
                    for key in (
                        "price_per_kwh",
                        "currency",
                        "distance_m",
                        "available_connectors",
                        "max_power_kw",
                    )
                },
                "ranking_reason": results.best_nearby.reason,
            }
        )
        recommendations.append(payload)
    return recommendations


def _best_nearby_rank_attributes(
    coordinator: CurbChargeCoordinator, station: ChargingStation | None
) -> dict[str, int | float | None]:
    """Expose a station's rank and candidate-relative score without coercion."""
    if station is None:
        results = (
            coordinator.recommendation_results
            if coordinator.last_update_success
            else None
        )
        rank, score = None, None
        candidate_count = (
            results.best_nearby.best_nearby_candidate_count
            if results is not None
            else None
        )
    else:
        rank, score, candidate_count = coordinator.best_nearby_rank_for(station)
    return {
        "best_nearby_rank": rank,
        "best_nearby_rank_score": score,
        "best_nearby_candidate_count": candidate_count,
    }


def _selection_station_data_attributes(station: ChargingStation) -> dict[str, Any]:
    """Return the shared, privacy-safe selected-station data contract."""
    tariff = station.tariff
    tariff_status = _station_tariff_status(station)
    currency = (
        tariff.currency
        if tariff is not None
        and tariff.currency.strip()
        and tariff_status is not TariffStatus.AMBIGUOUS
        else None
    )
    price_per_kwh = None
    if (
        tariff is not None
        and tariff.price_per_kwh is not None
        and tariff.effective_status is TariffStatus.AVAILABLE
        and tariff_status is TariffStatus.AVAILABLE
    ):
        price_per_kwh = float(tariff.price_per_kwh)
    return {
        "station_id": station.provider_id,
        "provider_station_id": station.provider_id,
        "provider": station.provider,
        "country_code": station.country_code,
        "name": station.name,
        "station_name": station.name,
        "operator": station.operator,
        "address": station.address,
        "latitude": station.latitude,
        "longitude": station.longitude,
        "distance_m": station.distance_m,
        "available_connectors": station.available_connectors,
        "total_connectors": station.total_connectors,
        "max_power_kw": station.max_power_kw,
        "price_per_kwh": price_per_kwh,
        "currency": currency,
        "provider_last_updated": _isoformat(station.last_updated),
    }


def create_station_sensors(
    coordinator: CurbChargeCoordinator, station: ChargingStation
) -> list[SensorEntity]:
    """Create the station's translated sensor entities."""
    return [
        AvailableConnectorsSensor(coordinator, station),
        AvailabilityPercentageSensor(coordinator, station),
        OccupiedConnectorsSensor(coordinator, station),
        UnavailableConnectorsSensor(coordinator, station),
        TotalConnectorsSensor(coordinator, station),
        MaximumChargingPowerSensor(coordinator, station),
        PricePerKwhSensor(coordinator, station),
        DistanceSensor(coordinator, station),
        StatusSummarySensor(coordinator, station),
        *(
            ConnectorGroupSensor(coordinator, station, connector)
            for connector in station.connectors
        ),
    ]


async def async_setup_dynamic_favorite_status_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry[Any],
    coordinator: CurbChargeCoordinator,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add or remove per-favorite entities from coordinator state only."""
    platform = async_get_current_platform()
    if not isinstance(platform, EntityPlatform):
        raise RuntimeError("CurbCharge favorite sensors require an active platform")

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    entities_by_favorite: dict[str, FavoriteStatusSensor] = {}
    sync_lock = asyncio.Lock()

    async def sync_entities() -> None:
        async with sync_lock:
            stations = coordinator.data or {}
            desired_ids = coordinator.favourite_ids & stations.keys()
            new_entities: list[FavoriteStatusSensor] = []
            for station_id in sorted(desired_ids - entities_by_favorite.keys()):
                station = stations[station_id]
                entity = FavoriteStatusSensor(coordinator, station, entry)
                if entity.unique_id is not None:
                    migrate_entity_unique_id(
                        hass,
                        entry,
                        platform.domain,
                        entity._legacy_unique_id,
                        entity.unique_id,
                    )
                parent_device_info = get_or_create_location_device(
                    device_registry, entry, station
                )
                if parent_device_info is not None:
                    entity.attach_to_location_device(station, *parent_device_info)
                entities_by_favorite[station_id] = entity
                new_entities.append(entity)
            if new_entities:
                async_add_entities(new_entities)

            for station_id in sorted(set(entities_by_favorite) - desired_ids):
                entity = entities_by_favorite.pop(station_id)
                entity_id = entity.entity_id
                if entity_id is not None and entity_id in platform.entities:
                    await platform.async_remove_entity(entity_id)
                if entity_id is not None and entity_id in entity_registry.entities:
                    entity_registry.async_remove(entity_id)

    @callback
    def handle_coordinator_update() -> None:
        hass.async_create_task(sync_entities())

    entry.async_on_unload(coordinator.async_add_listener(handle_coordinator_update))
    await sync_entities()


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def create_missing_connector_group_sensors(
    coordinator: CurbChargeCoordinator,
    station: ChargingStation,
    existing_unique_ids: set[str],
) -> list[ConnectorGroupSensor]:
    """Create only newly reported groups for an existing station."""
    entities: list[ConnectorGroupSensor] = []
    for connector in station.connectors:
        entity = ConnectorGroupSensor(coordinator, station, connector)
        if entity.unique_id not in existing_unique_ids:
            entities.append(entity)
    return entities
