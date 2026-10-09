"""Immutable models shared by providers and Home Assistant platforms."""

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


@dataclass(frozen=True, slots=True)
class ChargingTariff:
    """A tariff when the provider exposes an unambiguous price."""

    currency: str
    price_per_kwh: Decimal | None
    session_fee: Decimal | None
    price_per_minute: Decimal | None
    description: str | None
    source: str | None
    vat_percentage: Decimal | None = None


@dataclass(frozen=True, slots=True)
class ChargingStation:
    """A charging point and the information currently reported for it."""

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

    @property
    def available_connectors(self) -> int | None:
        """Return the exact available count, or None when not reported."""
        if not self.availability_reported:
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
        if not self.availability_reported:
            return None
        return sum(connector.count for connector in self.connectors)

    @property
    def has_available_connectors(self) -> bool | None:
        """Report whether availability is known to be positive."""
        if not self.availability_reported:
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
        if not self.availability_reported:
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
