"""Immutable models shared by providers and Home Assistant platforms."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import cast

from .const import (
    CONF_ALLOW_FALLBACK,
    CONF_CONNECTOR_TYPE_IDS,
    CONF_CONNECTOR_TYPES,
    CONF_EXCLUDED_OPERATORS,
    CONF_MAXIMUM_PRICE,
    CONF_MAXIMUM_PRICE_CURRENCY,
    CONF_MIN_POWER_KW,
    CONF_PERSONALIZATION_ENABLED,
    CONF_PERSONALIZATION_MIN_POWER_KW,
    CONF_PERSONALIZATION_VERSION,
    CONF_PREFERRED_OPERATORS,
    CONF_RANKING_PRESET,
    CONF_REQUIRE_AVAILABLE,
    CONF_UNKNOWN_DATA_POLICY,
    DEFAULT_CONNECTOR_TYPES,
    DEFAULT_MIN_POWER_KW,
    DEFAULT_REQUIRE_AVAILABLE,
    NDW_CONNECTOR_TYPE_CODES,
    NDW_CONNECTOR_TYPE_SELECTOR_OPTIONS,
    PERSONALIZATION_PROFILE_VERSION,
    PROVIDER_ECO_MOVEMENT_BE,
    SUPPORTED_PROVIDER_IDS,
)


class ConnectorStatus(StrEnum):
    """Known connector states."""

    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    RESERVED = "RESERVED"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    UNKNOWN = "UNKNOWN"


class ConnectorTypeId(StrEnum):
    """Provider-independent identifiers for recognized connector standards."""

    TYPE_2 = "IEC_62196_T2"
    CCS_COMBO_2 = "IEC_62196_T2_COMBO"
    CHADEMO = "CHADEMO"
    CEE_16A_SINGLE_PHASE = "IEC_60309_2_SINGLE_16"
    CEE_16A_THREE_PHASE = "IEC_60309_2_THREE_16"
    CEE_32A_THREE_PHASE = "IEC_60309_2_THREE_32"
    DOMESTIC_A = "DOMESTIC_A"


class PersonalizationRankingPreset(StrEnum):
    """Supported fixed ranking objectives for a personalization profile."""

    BALANCED = "balanced"
    LOWEST_PRICE = "lowest_price"
    SHORTEST_DISTANCE = "shortest_distance"
    HIGHEST_AVAILABILITY = "highest_availability"
    HIGHEST_POWER = "highest_power"


class PersonalizationUnknownDataPolicy(StrEnum):
    """Whether unknown preference values may enter the fallback pool."""

    EXCLUDE = "exclude"
    ALLOW_FALLBACK = "allow_fallback"


@dataclass(frozen=True, slots=True)
class OperatorReference:
    """A provider-scoped CPO ID or exact normalized operator-name key."""

    provider: str
    key: str

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDER_IDS:
            raise ValueError("Operator reference has an unsupported provider")
        if not isinstance(self.key, str):
            raise ValueError("Operator reference key must be a string")

        key = self.key.strip()
        if self.provider == PROVIDER_ECO_MOVEMENT_BE:
            key = " ".join(key.split()).casefold()
        if not key:
            raise ValueError("Operator reference key must not be empty")
        object.__setattr__(self, "key", key)

    @classmethod
    def from_config(cls, value: object) -> OperatorReference:
        """Validate one serialized provider-scoped operator reference."""
        if not isinstance(value, Mapping):
            raise ValueError("Operator reference must be a mapping")
        item = cast(Mapping[str, object], value)
        if set(item) != {"provider", "key"}:
            raise ValueError("Operator reference has invalid fields")
        provider = item.get("provider")
        key = item.get("key")
        if not isinstance(provider, str) or not isinstance(key, str):
            raise ValueError("Operator reference fields must be strings")
        return cls(provider, key)

    def as_config(self) -> dict[str, str]:
        """Return the stable provider-scoped key stored in config-entry options."""
        return {"provider": self.provider, "key": self.key}

    def as_input(self) -> str:
        """Return the stable line format used by the options-flow text fields."""
        return f"{self.provider}:{self.key}"


def parse_operator_reference_lines(value: object) -> tuple[OperatorReference, ...]:
    """Parse provider-qualified operator references, deduplicating normalized keys."""
    if not isinstance(value, str):
        raise ValueError("Operator references must be multiline text")

    references: list[OperatorReference] = []
    seen: set[OperatorReference] = set()
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        provider, separator, key = line.partition(":")
        if not separator:
            raise ValueError("Operator reference must include a provider")
        reference = OperatorReference(provider.strip(), key)
        if reference not in seen:
            references.append(reference)
            seen.add(reference)
    return tuple(references)


@dataclass(frozen=True, slots=True)
class PersonalizationProfile:
    """Versioned options for opt-in recommendation personalization."""

    enabled: bool = False
    connector_type_ids: tuple[ConnectorTypeId, ...] = ()
    minimum_power_kw: float | None = None
    preferred_operators: tuple[OperatorReference, ...] = ()
    excluded_operators: tuple[OperatorReference, ...] = ()
    maximum_price: Decimal | None = None
    maximum_price_currency: str | None = None
    ranking_preset: PersonalizationRankingPreset = PersonalizationRankingPreset.BALANCED
    allow_fallback: bool = True
    unknown_data_policy: PersonalizationUnknownDataPolicy = (
        PersonalizationUnknownDataPolicy.EXCLUDE
    )

    @classmethod
    def from_config(cls, value: object | None) -> PersonalizationProfile:
        """Validate a saved profile; a missing profile means disabled."""
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise ValueError("Personalization profile must be a mapping")

        data = cast(Mapping[str, object], value)
        expected_keys = {
            CONF_PERSONALIZATION_VERSION,
            CONF_PERSONALIZATION_ENABLED,
            CONF_CONNECTOR_TYPE_IDS,
            CONF_PERSONALIZATION_MIN_POWER_KW,
            CONF_PREFERRED_OPERATORS,
            CONF_EXCLUDED_OPERATORS,
            CONF_MAXIMUM_PRICE,
            CONF_MAXIMUM_PRICE_CURRENCY,
            CONF_RANKING_PRESET,
            CONF_ALLOW_FALLBACK,
            CONF_UNKNOWN_DATA_POLICY,
        }
        if set(data) != expected_keys:
            raise ValueError("Personalization profile has invalid fields")

        version = data[CONF_PERSONALIZATION_VERSION]
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != PERSONALIZATION_PROFILE_VERSION
        ):
            raise ValueError("Personalization profile version is unsupported")

        enabled = data[CONF_PERSONALIZATION_ENABLED]
        if not isinstance(enabled, bool):
            raise ValueError("Personalization enabled must be a boolean")

        raw_connector_type_ids = data[CONF_CONNECTOR_TYPE_IDS]
        if not isinstance(raw_connector_type_ids, (list, tuple)):
            raise ValueError("Personalization connector types must be a list")
        connector_type_ids: list[ConnectorTypeId] = []
        for raw_connector_type_id in raw_connector_type_ids:
            if not isinstance(raw_connector_type_id, str):
                raise ValueError("Personalization connector type is invalid")
            try:
                connector_type_id = ConnectorTypeId(raw_connector_type_id)
            except ValueError as err:
                raise ValueError(
                    "Personalization connector type is unsupported"
                ) from err
            if connector_type_id not in connector_type_ids:
                connector_type_ids.append(connector_type_id)

        raw_minimum_power = data[CONF_PERSONALIZATION_MIN_POWER_KW]
        if raw_minimum_power is None:
            minimum_power_kw = None
        elif isinstance(raw_minimum_power, bool) or not isinstance(
            raw_minimum_power, (int, float)
        ):
            raise ValueError("Personalization minimum power is invalid")
        else:
            try:
                minimum_power_kw = float(raw_minimum_power)
            except (OverflowError, ValueError) as err:
                raise ValueError("Personalization minimum power is invalid") from err
            if not math.isfinite(minimum_power_kw) or minimum_power_kw < 0:
                raise ValueError("Personalization minimum power is invalid")

        def operator_references(field_name: str) -> tuple[OperatorReference, ...]:
            raw_references = data[field_name]
            if not isinstance(raw_references, (list, tuple)):
                raise ValueError("Personalization operator references must be a list")
            references: list[OperatorReference] = []
            for raw_reference in raw_references:
                reference = OperatorReference.from_config(raw_reference)
                if reference not in references:
                    references.append(reference)
            return tuple(references)

        preferred_operators = operator_references(CONF_PREFERRED_OPERATORS)
        excluded_operators = operator_references(CONF_EXCLUDED_OPERATORS)
        if set(preferred_operators) & set(excluded_operators):
            raise ValueError("An operator cannot be both preferred and excluded")

        raw_price = data[CONF_MAXIMUM_PRICE]
        if raw_price is None:
            maximum_price = None
        elif not isinstance(raw_price, str) or not raw_price.strip():
            raise ValueError("Personalization maximum price must be a decimal string")
        else:
            try:
                maximum_price = Decimal(raw_price)
            except InvalidOperation as err:
                raise ValueError("Personalization maximum price is invalid") from err
            if not maximum_price.is_finite() or maximum_price < 0:
                raise ValueError("Personalization maximum price is invalid")

        raw_currency = data[CONF_MAXIMUM_PRICE_CURRENCY]
        if raw_currency is not None and not isinstance(raw_currency, str):
            raise ValueError("Personalization currency is invalid")
        if raw_currency is not None:
            if (
                len(raw_currency) != 3
                or not raw_currency.isascii()
                or not raw_currency.isalpha()
            ):
                raise ValueError("Personalization currency must be a 3-letter code")
            raw_currency = raw_currency.upper()
        if maximum_price is not None and raw_currency is None:
            raise ValueError("Maximum price requires an explicit currency")

        raw_ranking_preset = data[CONF_RANKING_PRESET]
        if not isinstance(raw_ranking_preset, str):
            raise ValueError("Personalization ranking preset is invalid")
        try:
            ranking_preset = PersonalizationRankingPreset(raw_ranking_preset)
        except (TypeError, ValueError) as err:
            raise ValueError("Personalization ranking preset is invalid") from err
        raw_unknown_data_policy = data[CONF_UNKNOWN_DATA_POLICY]
        if not isinstance(raw_unknown_data_policy, str):
            raise ValueError("Personalization unknown-data policy is invalid")
        try:
            unknown_data_policy = PersonalizationUnknownDataPolicy(
                raw_unknown_data_policy
            )
        except (TypeError, ValueError) as err:
            raise ValueError("Personalization unknown-data policy is invalid") from err
        allow_fallback = data[CONF_ALLOW_FALLBACK]
        if not isinstance(allow_fallback, bool):
            raise ValueError("Personalization fallback must be a boolean")

        return cls(
            enabled=enabled,
            connector_type_ids=tuple(connector_type_ids),
            minimum_power_kw=minimum_power_kw,
            preferred_operators=preferred_operators,
            excluded_operators=excluded_operators,
            maximum_price=maximum_price,
            maximum_price_currency=raw_currency,
            ranking_preset=ranking_preset,
            allow_fallback=allow_fallback,
            unknown_data_policy=unknown_data_policy,
        )

    def as_config(self) -> dict[str, object]:
        """Serialize the complete v1 profile with a lossless decimal price."""
        return {
            CONF_PERSONALIZATION_VERSION: PERSONALIZATION_PROFILE_VERSION,
            CONF_PERSONALIZATION_ENABLED: self.enabled,
            CONF_CONNECTOR_TYPE_IDS: [
                connector_type_id.value for connector_type_id in self.connector_type_ids
            ],
            CONF_PERSONALIZATION_MIN_POWER_KW: self.minimum_power_kw,
            CONF_PREFERRED_OPERATORS: [
                reference.as_config() for reference in self.preferred_operators
            ],
            CONF_EXCLUDED_OPERATORS: [
                reference.as_config() for reference in self.excluded_operators
            ],
            CONF_MAXIMUM_PRICE: (
                str(self.maximum_price) if self.maximum_price is not None else None
            ),
            CONF_MAXIMUM_PRICE_CURRENCY: self.maximum_price_currency,
            CONF_RANKING_PRESET: self.ranking_preset.value,
            CONF_ALLOW_FALLBACK: self.allow_fallback,
            CONF_UNKNOWN_DATA_POLICY: self.unknown_data_policy.value,
        }


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
    NOBIL = "nobil"


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
    connector_type_id: ConnectorTypeId | None = None

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
    operator_id: str | None = None

    def provider_data_age_seconds(self, now: datetime) -> int | None:
        """Return dynamic provider timestamp age in whole UTC seconds."""
        if (
            self.last_updated is None
            or self.last_updated_source
            not in (
                ProviderTimestampSource.NDW_FEATURE,
                ProviderTimestampSource.BELGIUM_EVSE_STATUS,
                ProviderTimestampSource.NOBIL,
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
    def comparable_price_per_kwh(self) -> tuple[str, Decimal] | None:
        """Return a safe normalized gross price for cross-station comparison."""
        tariff = self.tariff
        if (
            tariff is None
            or tariff.price_per_kwh is None
            or not tariff.price_per_kwh.is_finite()
            or not tariff.currency.strip()
            or tariff.effective_status is not TariffStatus.AVAILABLE
            or (
                self.tariff_status is not None
                and self.tariff_status is not TariffStatus.AVAILABLE
            )
        ):
            return None
        return tariff.currency.strip().upper(), tariff.price_per_kwh

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
