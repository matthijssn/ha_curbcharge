"""Immutable models shared by providers and Home Assistant platforms."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


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

    @property
    def available_connectors(self) -> int | None:
        """Return the exact available count, or None when not reported."""
        if not self.availability_reported or not self.connector_count_reported:
            return None
        total = 0
        for connector in self.connectors:
            if connector.available_count is not None:
                total += connector.available_count
            elif connector.status is ConnectorStatus.AVAILABLE:
                total += connector.count
            elif connector.status is ConnectorStatus.UNKNOWN:
                return None
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
