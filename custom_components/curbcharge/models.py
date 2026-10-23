"""Immutable models shared by providers and Home Assistant platforms."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import cast

from .const import (
    CONF_CONNECTOR_TYPES,
    CONF_MIN_POWER_KW,
    CONF_REQUIRE_AVAILABLE,
    DEFAULT_CONNECTOR_TYPES,
    DEFAULT_MIN_POWER_KW,
    DEFAULT_REQUIRE_AVAILABLE,
    NDW_CONNECTOR_TYPE_CODES,
    NDW_CONNECTOR_TYPE_SELECTOR_OPTIONS,
)


class ConnectorStatus(StrEnum):
    """Known connector states."""

    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    RESERVED = "RESERVED"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    UNKNOWN = "UNKNOWN"


class EVSEStatus(StrEnum):
    """Known operating states for an EVSE, distinct from connector states."""

    AVAILABLE = "AVAILABLE"
    BLOCKED = "BLOCKED"
    CHARGING = "CHARGING"
    INOPERATIVE = "INOPERATIVE"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    PLANNED = "PLANNED"
    REMOVED = "REMOVED"
    RESERVED = "RESERVED"
    UNKNOWN = "UNKNOWN"


class ProviderTimestampSource(StrEnum):
    """Origin of a station's provider record timestamp."""

    NDW_FEATURE = "ndw_feature"
    BELGIUM_EVSE_STATUS = "belgium_evse_status"
    BELGIUM_STATIC_FALLBACK = "belgium_static_fallback"


class TariffStatus(StrEnum):
    """How much of a provider tariff can be safely reported."""

    AVAILABLE = "available"
    NOT_REPORTED = "not_reported"
    MISSING = "missing"
    CONDITIONAL = "conditional"
    INACTIVE = "inactive"
    INCOMPLETE = "incomplete"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class DiscoveryFilterSettings:
    """Validated NDW connector-group filters from config-entry settings."""

    connector_types: tuple[str, ...] = DEFAULT_CONNECTOR_TYPES
    minimum_power_kw: float | None = DEFAULT_MIN_POWER_KW
    require_available: bool = DEFAULT_REQUIRE_AVAILABLE

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> DiscoveryFilterSettings:
        """Validate saved filter settings and apply defaults for older entries."""
        raw_connector_types = config.get(CONF_CONNECTOR_TYPES, DEFAULT_CONNECTOR_TYPES)
        if raw_connector_types is None:
            raw_connector_types = DEFAULT_CONNECTOR_TYPES
        if not isinstance(raw_connector_types, (list, tuple)):
            raise ValueError("Invalid connector type filters")

        connector_types: list[str] = []
        selector_to_code = dict(NDW_CONNECTOR_TYPE_SELECTOR_OPTIONS)
        for connector_type in raw_connector_types:
            if not isinstance(connector_type, str):
                raise ValueError("Invalid connector type filter")
            connector_type = selector_to_code.get(connector_type, connector_type)
            if connector_type not in NDW_CONNECTOR_TYPE_CODES:
                raise ValueError("Invalid connector type filter")
            if connector_type not in connector_types:
                connector_types.append(connector_type)

        raw_minimum_power = config.get(CONF_MIN_POWER_KW, DEFAULT_MIN_POWER_KW)
        if raw_minimum_power is None:
            minimum_power_kw = None
        elif (
            isinstance(raw_minimum_power, bool)
            or not isinstance(raw_minimum_power, (int, float))
            or not math.isfinite(raw_minimum_power)
            or raw_minimum_power <= 0
        ):
            raise ValueError("Minimum power must be finite and positive")
        else:
            minimum_power_kw = float(raw_minimum_power)

        require_available = config.get(
            CONF_REQUIRE_AVAILABLE, DEFAULT_REQUIRE_AVAILABLE
        )
        if not isinstance(require_available, bool):
            raise ValueError("Require-available filter must be a boolean")

        return cls(tuple(connector_types), minimum_power_kw, require_available)

    def matches_station(self, station: ChargingStation) -> bool:
        """Match if one connector group satisfies every active criterion."""
        if (
            not self.connector_types
            and self.minimum_power_kw is None
            and not self.require_available
        ):
            return True

        return any(
            (
                not self.connector_types
                or connector.connector_type in self.connector_types
            )
            and (
                self.minimum_power_kw is None
                or (
                    connector.max_power_kw is not None
                    and connector.max_power_kw >= self.minimum_power_kw
                )
            )
            and (
                not self.require_available
                or (
                    connector.available_count is not None
                    and connector.available_count > 0
                )
            )
            for connector in station.connectors
        )

    def as_config(self) -> dict[str, object]:
        """Return a JSON-safe config-entry representation."""
        return {
            CONF_CONNECTOR_TYPES: list(self.connector_types),
            CONF_MIN_POWER_KW: self.minimum_power_kw,
            CONF_REQUIRE_AVAILABLE: self.require_available,
        }


@dataclass(frozen=True, slots=True)
class ChargingConnector:
    """A connector or provider-reported group of equivalent connectors."""

    provider_connector_id: str
    connector_type: str | None
    status: ConnectorStatus
    max_power_kw: float | None
    count: int = 1
    available_count: int | None = None
    connector_format: str | None = None
    power_type: str | None = None
    tariff_ids: tuple[str, ...] = ()
    tariffs: tuple[ChargingTariff, ...] = ()
    tariff_status: TariffStatus | None = None

    @property
    def known_available_count(self) -> int | None:
        """Return reported availability, or infer it only from a known status."""
        if self.available_count is not None:
            return self.available_count
        if self.status is ConnectorStatus.UNKNOWN:
            return None
        return self.count if self.status is ConnectorStatus.AVAILABLE else 0


@dataclass(frozen=True, slots=True)
class ChargingTariff:
    """Normalized provider tariff, including why its price may be unresolved."""

    currency: str
    price_per_kwh: Decimal | None
    session_fee: Decimal | None
    price_per_minute: Decimal | None
    description: str | None
    source: str | None
    vat_percentage: Decimal | None = None
    tariff_id: str | None = None
    tariff_type: str | None = None
    status: TariffStatus | None = None

    @property
    def effective_status(self) -> TariffStatus:
        """Return parsed status or infer it for providers without tariff reasons."""
        if self.status is not None:
            return self.status
        return (
            TariffStatus.AVAILABLE
            if self.price_per_kwh is not None
            else TariffStatus.INCOMPLETE
        )


@dataclass(frozen=True, slots=True)
class ChargingLocation:
    """A provider-identified physical charging site."""

    provider_id: str
    provider: str
    provider_name: str
    country_code: str
    name: str
    latitude: float
    longitude: float
    address: str | None
    operator: str | None
    last_updated: datetime | None


@dataclass(frozen=True, slots=True)
class ChargingStation:
    """A charging unit with country-neutral location and connector data."""

    provider_id: str
    name: str
    latitude: float
    longitude: float
    address: str | None
    operator: str | None
    distance_m: int | None
    connectors: tuple[ChargingConnector, ...]
    tariff: ChargingTariff | None
    last_updated: datetime | None
    provider: str = "ndw_dot_nl"
    availability_reported: bool = True
    is_open: bool | None = None
    is_stale: bool = False
    country_code: str = "NL"
    location: ChargingLocation | None = None
    provider_name: str = "NDW DOT-NL"
    evse_id: str | None = None
    evse_status: EVSEStatus | None = None
    connector_count_reported: bool = True
    tariff_status: TariffStatus | None = None
    last_updated_source: ProviderTimestampSource | None = None

    def provider_data_age_seconds(self, now: datetime) -> int | None:
        """Return dynamic provider timestamp age in whole UTC seconds."""
        if (
            self.last_updated is None
            or self.last_updated_source
            not in (
                ProviderTimestampSource.NDW_FEATURE,
                ProviderTimestampSource.BELGIUM_EVSE_STATUS,
            )
            or self.last_updated.utcoffset() is None
            or now.utcoffset() is None
        ):
            return None
        age_seconds = (
            now.astimezone(UTC) - self.last_updated.astimezone(UTC)
        ).total_seconds()
        return None if age_seconds < 0 else round(age_seconds)

    @property
    def available_connectors(self) -> int | None:
        """Return the exact available count, or None when not reported."""
        if not self.availability_reported or not self.connector_count_reported:
            return None
        total = 0
        for connector in self.connectors:
            available = connector.known_available_count
            if available is None:
                return None
            total += available
        return total

    @property
    def total_connectors(self) -> int | None:
        """Return the exact total count, or None when not reported."""
        if not self.connector_count_reported:
            return None
        return sum(connector.count for connector in self.connectors)

    @property
    def has_available_connectors(self) -> bool | None:
        """Report whether availability is known to be positive."""
        if not self.availability_reported:
            return None
        if self.evse_status is not None:
            if self.evse_status is not EVSEStatus.UNKNOWN:
                return self.evse_status is EVSEStatus.AVAILABLE
            if not self.connector_count_reported:
                return None
        if any(
            connector.available_count is not None
            and connector.available_count > 0
            or connector.available_count is None
            and connector.status is ConnectorStatus.AVAILABLE
            and connector.count > 0
            for connector in self.connectors
        ):
            return True
        if any(
            connector.status is ConnectorStatus.UNKNOWN
            and connector.available_count is None
            and connector.count > 0
            for connector in self.connectors
        ):
            return None
        return False

    @property
    def unavailable_connectors(self) -> int | None:
        """Return connectors not reported as available."""
        available = self.available_connectors
        total = self.total_connectors
        if available is None or total is None:
            return None
        return total - available

    @property
    def occupied_connectors(self) -> int | None:
        """Return occupancy only when it can be known without inference."""
        unknown = self.unknown_connectors
        if not self.availability_reported or unknown is None or unknown > 0:
            return None
        return sum(
            connector.count
            for connector in self.connectors
            if connector.status is ConnectorStatus.OCCUPIED
        )

    @property
    def reserved_connectors(self) -> int | None:
        """Return the reserved count when every connector status is resolved."""
        if not self.availability_reported or self.unknown_connectors is None:
            return None
        if self.unknown_connectors > 0:
            return None
        return sum(
            connector.count
            for connector in self.connectors
            if connector.status is ConnectorStatus.RESERVED
        )

    @property
    def out_of_service_connectors(self) -> int | None:
        """Return the out-of-service count when every status is resolved."""
        if not self.availability_reported or self.unknown_connectors is None:
            return None
        if self.unknown_connectors > 0:
            return None
        return sum(
            connector.count
            for connector in self.connectors
            if connector.status is ConnectorStatus.OUT_OF_SERVICE
        )

    @property
    def unknown_connectors(self) -> int | None:
        """Count connector states the provider did not distinguish."""
        if not self.availability_reported or not self.connector_count_reported:
            return None
        return sum(
            connector.count
            - (
                connector.available_count
                if connector.available_count is not None
                else 0
            )
            for connector in self.connectors
            if connector.status is ConnectorStatus.UNKNOWN
        )

    @property
    def max_power_kw(self) -> float | None:
        """Return the highest reported connector power."""
        powers = [
            connector.max_power_kw
            for connector in self.connectors
            if connector.max_power_kw is not None
        ]
        return max(powers, default=None)

    @property
    def connector_types(self) -> tuple[str, ...]:
        """Return the unique reported connector types."""
        return tuple(
            sorted(
                {
                    connector.connector_type
                    for connector in self.connectors
                    if connector.connector_type
                }
            )
        )


@dataclass(frozen=True, slots=True)
class FavouriteStationReference:
    """Provider identity and last-known lookup coordinates for one favorite."""

    provider: str
    provider_id: str
    name: str
    latitude: float
    longitude: float
    location_id: str | None = None

    @classmethod
    def from_station(cls, station: ChargingStation) -> FavouriteStationReference:
        """Create a stable favorite reference from a discovered station."""
        location = station.location
        return cls(
            provider=station.provider,
            provider_id=station.provider_id,
            name=station.provider_id,
            latitude=station.latitude,
            longitude=station.longitude,
            location_id=(
                location.provider_id
                if (
                    location is not None
                    and location.provider == station.provider
                    and location.provider_id != station.provider_id
                )
                else None
            ),
        )

    def as_config(self) -> dict[str, object]:
        """Return stable identity and lookup hints, never mutable display data."""
        return {
            "provider": self.provider,
            "provider_id": self.provider_id,
            "name": self.provider_id,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "location_id": self.location_id,
        }


@dataclass(frozen=True, slots=True)
class StationDiscoveryResult:
    """Normal nearby results and additive favourite results from one provider."""

    nearby: tuple[ChargingStation, ...]
    favourites: tuple[ChargingStation, ...]


def parse_favourite_references(
    value: object, provider: str
) -> tuple[FavouriteStationReference, ...]:
    """Validate favorite identity and lookup hints, ignoring legacy display data."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("Favourite references must be a list")

    favourites: list[FavouriteStationReference] = []
    seen_ids: set[str] = set()
    for item_value in value:
        if not isinstance(item_value, Mapping):
            raise ValueError("Favourite reference must be a mapping")
        item = cast(Mapping[str, object], item_value)
        item_provider = item.get("provider")
        provider_id = item.get("provider_id")
        latitude = item.get("latitude")
        longitude = item.get("longitude")
        location_id = item.get("location_id")
        if (
            item_provider != provider
            or not isinstance(provider_id, str)
            or not provider_id.strip()
            or provider_id in seen_ids
            or not isinstance(latitude, (int, float))
            or isinstance(latitude, bool)
            or not isinstance(longitude, (int, float))
            or isinstance(longitude, bool)
            or (
                location_id is not None
                and (not isinstance(location_id, str) or not location_id)
            )
        ):
            raise ValueError("Favourite reference is invalid")

        latitude = float(latitude)
        longitude = float(longitude)
        if (
            not math.isfinite(latitude)
            or not math.isfinite(longitude)
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            raise ValueError("Favourite coordinates are invalid")

        favourites.append(
            FavouriteStationReference(
                provider=provider,
                provider_id=provider_id,
                name=provider_id,
                latitude=latitude,
                longitude=longitude,
                location_id=location_id,
            )
        )
        seen_ids.add(provider_id)

    return tuple(favourites)
