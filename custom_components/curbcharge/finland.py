"""Async client for Fintraffic/Digitraffic's Finland AFIR API."""

from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import cast

import aiohttp
from homeassistant.core import HomeAssistant

from .const import (
    COUNTRY_FI,
    DOMAIN,
    MAX_MAX_STATIONS,
    MAX_SEARCH_RADIUS_M,
    MIN_MAX_STATIONS,
    MIN_SEARCH_RADIUS_M,
    PROVIDER_DIGITRAFFIC_AFIR_FI,
    PROVIDER_DIGITRAFFIC_AFIR_FI_NAME,
)
from .geography import distance_m
from .models import (
    ChargingConnector,
    ChargingLocation,
    ChargingStation,
    ChargingTariff,
    ConnectorStatus,
    ConnectorTypeId,
    DiscoveryFilterSettings,
    EVSEStatus,
    FavouriteStationReference,
    ProviderTimestampSource,
    StationDiscoveryResult,
    TariffStatus,
)
from .provider import (
    BaseChargingProvider,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
    retry_after_seconds,
)

_API_BASE_URL = "https://afir.digitraffic.fi/api/charging-network/v1"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
_PAGE_LIMIT = "500"
# ponytail: generous bound against a misbehaving/looping cursor; AFIR does not
# publish a numeric Finland total, raise this only if a real refresh needs it.
_MAX_PAGES = 50
_REQUEST_LIMITER_DATA_KEY = "finland_request_limiter"
# ponytail: 1.1s pacing stays comfortably under Digitraffic's documented 60
# requests/minute-per-IP guidance; shorten only if a higher limit is published.
_MIN_REQUEST_INTERVAL_SECONDS = 1.1
# ponytail: locations/operators/tariffs change far less often than live EVSE
# status; caching them keeps a full-country poll well under the rate guidance.
# Statuses are always fetched fresh. Shorten if operators need faster updates.
_CATALOG_CACHE_SECONDS = 15 * 60
_EVSE_STATUS_MAP = {
    "available": EVSEStatus.AVAILABLE,
    "blocked": EVSEStatus.BLOCKED,
    "charging": EVSEStatus.CHARGING,
    "inoperative": EVSEStatus.INOPERATIVE,
    "outoforder": EVSEStatus.OUT_OF_ORDER,
    "planned": EVSEStatus.PLANNED,
    "removed": EVSEStatus.REMOVED,
    "reserved": EVSEStatus.RESERVED,
}
# ponytail: maps AFIR's OCPI 2.2.1 connector "standard" values onto CurbCharge's
# existing ConnectorTypeId enum; standards without a local equivalent (CHAOJI,
# GB/T, MCS, newer Type 3/NACS/pantograph variants) stay None (type preserved
# as free text). TESLA_R/TESLA_S both collapse onto the single generic TESLA
# value already used elsewhere in this integration.
_CONNECTOR_TYPE_MAP = {
    "CHADEMO": ConnectorTypeId.CHADEMO,
    "DOMESTIC_A": ConnectorTypeId.DOMESTIC_A,
    "IEC_62196_T1": ConnectorTypeId.TYPE_1,
    "IEC_62196_T1_COMBO": ConnectorTypeId.CCS_COMBO_1,
    "IEC_62196_T2": ConnectorTypeId.TYPE_2,
    "IEC_62196_T2_COMBO": ConnectorTypeId.CCS_COMBO_2,
    "IEC_60309_2_single_16": ConnectorTypeId.CEE_16A_SINGLE_PHASE,
    "IEC_60309_2_three_16": ConnectorTypeId.CEE_16A_THREE_PHASE,
    "IEC_60309_2_three_32": ConnectorTypeId.CEE_32A_THREE_PHASE,
    "NEMA_5_20": ConnectorTypeId.NEMA_5_20,
    "NEMA_14_50": ConnectorTypeId.NEMA_14_50,
    "TESLA_R": ConnectorTypeId.TESLA,
    "TESLA_S": ConnectorTypeId.TESLA,
}
_TARIFF_STATUS_PRIORITY = (
    TariffStatus.CONDITIONAL,
    TariffStatus.INACTIVE,
    TariffStatus.MISSING,
    TariffStatus.AMBIGUOUS,
    TariffStatus.INCOMPLETE,
)


def is_valid_finland_search_radius(radius_m: int) -> bool:
    """Return whether a search radius is within the supported bounds."""
    return MIN_SEARCH_RADIUS_M <= radius_m <= MAX_SEARCH_RADIUS_M


class FinlandRequestLimiter:
    """Pace anonymous AFIR requests shared by this Home Assistant instance."""

    def __init__(self) -> None:
        """Initialize limiter state."""
        self._lock = asyncio.Lock()
        self._next_allowed_at = 0.0
        self._retry_after_until = 0.0

    async def async_wait(self) -> None:
        """Wait until the next request may safely be sent."""
        while True:
            async with self._lock:
                now = time.monotonic()
                allowed_at = max(self._next_allowed_at, self._retry_after_until)
                delay = allowed_at - now
                if delay <= 0:
                    self._next_allowed_at = now + _MIN_REQUEST_INTERVAL_SECONDS
                    return
            await asyncio.sleep(delay)

    def defer_for_retry_after(self, seconds: int | None) -> None:
        """Pause every client sharing this limiter for the server's Retry-After."""
        if seconds is not None and seconds > 0:
            self._retry_after_until = max(
                self._retry_after_until, time.monotonic() + seconds
            )


def _shared_request_limiter(hass: HomeAssistant) -> FinlandRequestLimiter:
    """Return the one request limiter shared by Finland clients in this HA instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    limiter = domain_data.get(_REQUEST_LIMITER_DATA_KEY)
    if limiter is None:
        limiter = FinlandRequestLimiter()
        domain_data[_REQUEST_LIMITER_DATA_KEY] = limiter
    if not isinstance(limiter, FinlandRequestLimiter):
        raise RuntimeError("CurbCharge Finland request limiter has an invalid type")
    return limiter


def create_finland_provider(
    session: aiohttp.ClientSession,
    hass: HomeAssistant | None = None,
) -> FinlandChargingProvider:
    """Create the Finland AFIR provider, sharing a limiter where possible."""
    limiter = (
        _shared_request_limiter(hass) if hass is not None else FinlandRequestLimiter()
    )
    return FinlandChargingProvider(session, limiter)


class FinlandChargingProvider(BaseChargingProvider):
    """Fetch and normalize Fintraffic/Digitraffic AFIR data for Finland."""

    def __init__(
        self, session: aiohttp.ClientSession, request_limiter: FinlandRequestLimiter
    ) -> None:
        """Initialize the client using Home Assistant's shared session."""
        self._session = session
        self._request_limiter = request_limiter
        self._catalog: (
            tuple[dict[str, dict[str, object]], dict[str, ChargingTariff]] | None
        ) = None
        self._catalog_cached_at: float | None = None

    @property
    def provider_name(self) -> str:
        """Return the display name of the provider."""
        return PROVIDER_DIGITRAFFIC_AFIR_FI_NAME

    @property
    def provider_id(self) -> str:
        """Return the stable Finland provider identifier."""
        return PROVIDER_DIGITRAFFIC_AFIR_FI

    @property
    def country_code(self) -> str:
        """Return the country served by this provider."""
        return COUNTRY_FI

    async def async_validate_connection(
        self, latitude: float, longitude: float, radius_m: int
    ) -> None:
        """Fetch and parse one page of Finnish locations without caching it."""
        _validate_search(latitude, longitude, radius_m, MIN_MAX_STATIONS)
        payload = await self._async_request(
            "/locations", {"operatorCountryCode": "FI", "limit": _PAGE_LIMIT}
        )
        mapping = _string_mapping(payload, "locations response")
        for feature in _list_field(mapping, "features"):
            _stations_from_feature(feature, latitude, longitude, {}, {})

    async def async_get_stations(
        self,
        latitude: float,
        longitude: float,
        radius_m: int,
        max_stations: int,
        favourites: tuple[FavouriteStationReference, ...] = (),
        discovery_filters: DiscoveryFilterSettings | None = None,
        include_unknown: bool = True,
    ) -> StationDiscoveryResult:
        """Fetch all Finnish stations and select nearby and favourited ones."""
        _validate_search(latitude, longitude, radius_m, max_stations)
        if any(favourite.provider != self.provider_id for favourite in favourites):
            raise InvalidProviderResponseError(
                "A saved favourite belongs to a different provider"
            )

        locations_by_id, tariffs = await self._async_get_catalog()
        statuses = await self._async_get_statuses()

        all_stations: list[ChargingStation] = []
        for feature in locations_by_id.values():
            all_stations.extend(
                _stations_from_feature(feature, latitude, longitude, statuses, tariffs)
            )
        stations_by_id = {station.provider_id: station for station in all_stations}

        favourite_ids = {favourite.provider_id for favourite in favourites}
        nearby_candidates = [
            station
            for station in all_stations
            if station.distance_m is not None
            and station.distance_m <= radius_m
            and (
                include_unknown
                or station.availability_reported
                or station.provider_id in favourite_ids
            )
        ]
        nearby_candidates.sort(
            key=lambda station: (station.distance_m, station.provider_id)
        )
        nearby = tuple(nearby_candidates[:max_stations])

        favourite_stations = tuple(
            station
            for favourite in favourites
            if (station := stations_by_id.get(favourite.provider_id)) is not None
        )
        return StationDiscoveryResult(nearby=nearby, favourites=favourite_stations)

    async def _async_get_catalog(
        self,
    ) -> tuple[dict[str, dict[str, object]], dict[str, ChargingTariff]]:
        """Return cached Finnish locations and tariffs, refreshing when stale."""
        now = time.monotonic()
        if (
            self._catalog is not None
            and self._catalog_cached_at is not None
            and now - self._catalog_cached_at < _CATALOG_CACHE_SECONDS
        ):
            return self._catalog

        raw_features = await self._async_paginated(
            "/locations", {"operatorCountryCode": "FI"}, "features"
        )
        locations_by_id: dict[str, dict[str, object]] = {}
        for raw_feature in raw_features:
            feature = _string_mapping(raw_feature, "location feature")
            properties = _string_mapping(feature.get("properties"), "location")
            location_id = _required_string(properties.get("id"), "location id")
            locations_by_id[location_id] = feature

        raw_tariffs = await self._async_paginated(
            "/tariffs", {"operatorCountryCode": "FI"}, "tariffs"
        )
        now_utc = datetime.now(UTC)
        tariffs: dict[str, ChargingTariff] = {}
        for raw_tariff in raw_tariffs:
            record = _string_mapping(raw_tariff, "tariff")
            tariff_id = _required_string(record.get("id"), "tariff id")
            tariffs[tariff_id] = _parse_tariff_record(record, now_utc)

        catalog = (locations_by_id, tariffs)
        self._catalog = catalog
        self._catalog_cached_at = time.monotonic()
        return catalog

    async def _async_get_statuses(
        self,
    ) -> dict[str, tuple[EVSEStatus, datetime | None]]:
        """Fetch current Finnish EVSE statuses without caching them."""
        raw_statuses = await self._async_paginated(
            "/locations/statuses", {"operatorCountryCode": "FI"}, "statuses"
        )
        statuses: dict[str, tuple[EVSEStatus, datetime | None]] = {}
        for raw_status in raw_statuses:
            record = _string_mapping(raw_status, "status")
            evse_id = _required_string(record.get("evseId"), "status EVSE id")
            status = _evse_status(record.get("status"))
            last_updated_at = _optional_datetime(
                record.get("lastUpdatedAt"), "status lastUpdatedAt"
            )
            statuses[evse_id] = (status, last_updated_at)
        return statuses

    async def _async_paginated(
        self, path: str, params: dict[str, str], items_key: str
    ) -> list[object]:
        """Follow AFIR's cursor pagination, bounded against a repeating cursor."""
        collected: list[object] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(_MAX_PAGES):
            query = {**params, "limit": _PAGE_LIMIT}
            if cursor is not None:
                query["cursor"] = cursor
            payload = await self._async_request(path, query)
            mapping = _string_mapping(payload, "paginated response")
            collected.extend(_list_field(mapping, items_key))
            pagination_value = mapping.get("pagination")
            pagination = (
                _string_mapping(pagination_value, "pagination")
                if pagination_value is not None
                else {}
            )
            next_cursor = pagination.get("nextCursor")
            if next_cursor is None:
                return collected
            if not isinstance(next_cursor, str) or next_cursor in seen_cursors:
                raise InvalidProviderResponseError(
                    "AFIR returned a repeating pagination cursor"
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise InvalidProviderResponseError(
            "AFIR pagination exceeded the expected page bound"
        )

    async def _async_request(self, path: str, params: dict[str, str]) -> object:
        """Send one anonymous, rate-limited GET request and parse its JSON body."""
        await self._request_limiter.async_wait()
        try:
            async with self._session.get(
                f"{_API_BASE_URL}{path}",
                params=params,
                headers={
                    "Digitraffic-User": "ha_curbcharge",
                    "Accept-Encoding": "gzip",
                },
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                if response.status == 429:
                    retry_after = retry_after_seconds(
                        response.headers.get("Retry-After")
                    )
                    self._request_limiter.defer_for_retry_after(retry_after)
                    raise ProviderRateLimitError(
                        "AFIR rate-limited the request", retry_after
                    )
                if 500 <= response.status < 600:
                    raise ProviderConnectionError(
                        "AFIR returned a temporary server error"
                    )
                # ponytail: AFIR is unauthenticated open data; treat any other
                # non-2xx (including an unexpected 401/403) as a connection
                # problem rather than an authentication failure, since there is
                # no user-supplied credential to correct.
                if not 200 <= response.status < 300:
                    raise ProviderConnectionError(
                        "AFIR returned an unsuccessful response"
                    )
                body = await response.read()
        except (ProviderConnectionError, ProviderRateLimitError):
            raise
        except (TimeoutError, aiohttp.ClientError, OSError):
            raise ProviderConnectionError(
                "Could not connect to the Finland charging-data provider"
            ) from None

        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise InvalidProviderResponseError("AFIR returned invalid JSON") from err


def _validate_search(
    latitude: float, longitude: float, radius_m: int, limit: int
) -> None:
    if (
        isinstance(latitude, bool)
        or isinstance(longitude, bool)
        or not isinstance(latitude, (int, float))
        or not isinstance(longitude, (int, float))
        or not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        raise UnsupportedLocationError("The Finland search location is invalid")
    if not is_valid_finland_search_radius(radius_m):
        raise UnsupportedLocationError("The Finland search radius is unsupported")
    if not MIN_MAX_STATIONS <= limit <= MAX_MAX_STATIONS:
        raise UnsupportedLocationError("The Finland station limit is unsupported")


def _stations_from_feature(
    raw_feature: object,
    latitude: float,
    longitude: float,
    statuses: dict[str, tuple[EVSEStatus, datetime | None]],
    tariffs: dict[str, ChargingTariff],
) -> list[ChargingStation]:
    """Build one ChargingStation per EVSE at a location feature."""
    feature = _string_mapping(raw_feature, "location feature")
    properties = _string_mapping(feature.get("properties"), "location")
    operator = _string_mapping(properties.get("operator"), "operator")
    operator_country_code = _required_string(
        operator.get("countryCode"), "operator country code"
    )
    if operator_country_code.upper() != COUNTRY_FI:
        # ponytail: AFIR is a shared multi-country dataset; defensively drop
        # any record outside Finland even though requests already filter by
        # operatorCountryCode=FI.
        return []

    location_id = _required_string(properties.get("id"), "location id")
    location_coordinates = _geometry_coordinates(
        feature.get("geometry"), "location geometry"
    )
    location_name = _optional_string(properties.get("name"), "location name")
    address = _format_address(properties.get("address"))
    operator_details = _string_mapping(operator.get("details"), "operator details")
    operator_name = _optional_string(operator_details.get("name"), "operator name")
    operator_id = _optional_string(operator.get("id"), "operator id")
    location_modified_at = _optional_datetime(
        properties.get("modifiedAt"), "location modifiedAt"
    )
    base_name = location_name or address or location_id

    location = ChargingLocation(
        provider_id=location_id,
        provider=PROVIDER_DIGITRAFFIC_AFIR_FI,
        provider_name=PROVIDER_DIGITRAFFIC_AFIR_FI_NAME,
        country_code=COUNTRY_FI,
        name=base_name,
        latitude=location_coordinates[0],
        longitude=location_coordinates[1],
        address=address,
        operator=operator_name,
        last_updated=location_modified_at,
    )

    stations: list[ChargingStation] = []
    for raw_evse in _list_field(properties, "evses"):
        evse = _string_mapping(raw_evse, "EVSE")
        evse_id = _required_string(evse.get("id"), "EVSE id")
        status, status_updated_at = statuses.get(evse_id, (EVSEStatus.UNKNOWN, None))
        if status is EVSEStatus.REMOVED:
            continue

        physical_reference = _optional_string(
            evse.get("physicalReference"), "EVSE physical reference"
        )
        name = (
            base_name
            if physical_reference is None
            else (f"{base_name} ({physical_reference})")
        )
        evse_modified_at = _optional_datetime(evse.get("modifiedAt"), "EVSE modifiedAt")
        connectors = _connectors_from_evse(
            evse_id, _list_field(evse, "connectors"), tariffs
        )
        availability_reported = status_updated_at is not None
        last_updated_source: ProviderTimestampSource | None
        if availability_reported:
            last_updated = status_updated_at
            last_updated_source = ProviderTimestampSource.FINLAND_EVSE_STATUS
        else:
            last_updated = evse_modified_at
            last_updated_source = (
                ProviderTimestampSource.FINLAND_STATIC_FALLBACK
                if evse_modified_at is not None
                else None
            )

        station = ChargingStation(
            provider_id=evse_id,
            name=name,
            latitude=location_coordinates[0],
            longitude=location_coordinates[1],
            address=address,
            operator=operator_name,
            distance_m=distance_m(
                latitude, longitude, location_coordinates[0], location_coordinates[1]
            ),
            connectors=connectors,
            tariff=None,
            last_updated=last_updated,
            provider=PROVIDER_DIGITRAFFIC_AFIR_FI,
            availability_reported=availability_reported,
            evse_status=status,
            is_open=None,
            country_code=COUNTRY_FI,
            location=location,
            provider_name=PROVIDER_DIGITRAFFIC_AFIR_FI_NAME,
            evse_id=evse_id,
            connector_count_reported=True,
            last_updated_source=last_updated_source,
            operator_id=operator_id,
        )
        stations.append(_with_station_tariff(station))
    return stations


def _connectors_from_evse(
    evse_id: str,
    raw_connectors: list[object],
    tariffs: dict[str, ChargingTariff],
) -> tuple[ChargingConnector, ...]:
    connectors: list[ChargingConnector] = []
    for index, raw_connector in enumerate(raw_connectors):
        connector = _string_mapping(raw_connector, "connector")
        standard = _optional_string(connector.get("standard"), "connector standard")
        connector_format = _optional_string(connector.get("format"), "connector format")
        power_type = _optional_string(
            connector.get("powerType"), "connector power type"
        )
        max_power_watts = connector.get("maxElectricPower")
        max_power_kw = _optional_nonnegative_power(max_power_watts)
        raw_tariff_ids = connector.get("tariffIds")
        if raw_tariff_ids is not None and not isinstance(raw_tariff_ids, list):
            raise InvalidProviderResponseError(
                "AFIR returned an invalid connector tariffIds list"
            )
        tariff_ids = tuple(
            tariff_id
            for tariff_id in (raw_tariff_ids or [])
            if isinstance(tariff_id, str)
        )
        resolved = tuple(
            tariffs[tariff_id] for tariff_id in tariff_ids if tariff_id in tariffs
        )
        connectors.append(
            ChargingConnector(
                provider_connector_id=f"{evse_id}:connector:{index}",
                connector_type=standard,
                connector_type_id=(
                    _CONNECTOR_TYPE_MAP.get(standard) if standard else None
                ),
                status=ConnectorStatus.UNKNOWN,
                max_power_kw=max_power_kw,
                count=1,
                available_count=None,
                connector_format=connector_format,
                power_type=power_type,
                tariff_ids=tariff_ids,
                tariffs=resolved,
                tariff_status=_connector_tariff_status(tariff_ids, resolved),
            )
        )
    return tuple(connectors)


def _connector_tariff_status(
    tariff_ids: tuple[str, ...], resolved: tuple[ChargingTariff, ...]
) -> TariffStatus:
    if not tariff_ids:
        return TariffStatus.NOT_REPORTED
    if len(resolved) != len(tariff_ids):
        return TariffStatus.MISSING
    statuses = {tariff.effective_status for tariff in resolved}
    if len(statuses) > 1:
        for status in _TARIFF_STATUS_PRIORITY:
            if status in statuses:
                return status
        # ponytail: every effective_status this module produces is already one
        # of the priority values above, so this is a defensive fallback, not a
        # reachable outcome; kept in case a future tariff status is added.
        return TariffStatus.INCOMPLETE  # pragma: no cover
    status = next(iter(statuses))
    if status is not TariffStatus.AVAILABLE:
        return status
    signatures = {_tariff_signature(tariff) for tariff in resolved}
    return TariffStatus.AVAILABLE if len(signatures) == 1 else TariffStatus.AMBIGUOUS


def _with_station_tariff(station: ChargingStation) -> ChargingStation:
    """Combine a station's per-connector tariffs into one station-level tariff."""
    connectors = station.connectors
    if not connectors:
        return replace(station, tariff_status=TariffStatus.NOT_REPORTED)

    statuses = [connector.tariff_status for connector in connectors]
    if all(status is TariffStatus.AVAILABLE for status in statuses):
        first_resolved = [
            connector.tariffs[0] for connector in connectors if connector.tariffs
        ]
        if first_resolved and all(
            _tariff_signature(tariff) == _tariff_signature(first_resolved[0])
            for tariff in first_resolved[1:]
        ):
            linked = [
                tariff for connector in connectors for tariff in connector.tariffs
            ]
            tariff_ids = {tariff.tariff_id for tariff in linked}
            return replace(
                station,
                tariff=replace(
                    first_resolved[0],
                    tariff_id=next(iter(tariff_ids)) if len(tariff_ids) == 1 else None,
                ),
                tariff_status=TariffStatus.AVAILABLE,
            )
        return replace(station, tariff_status=TariffStatus.AMBIGUOUS)

    known = {status for status in statuses if status is not None}
    if not known or known == {TariffStatus.NOT_REPORTED}:
        return replace(station, tariff_status=TariffStatus.NOT_REPORTED)
    for status in _TARIFF_STATUS_PRIORITY:
        if status in known:
            return replace(station, tariff_status=status)
    return replace(station, tariff_status=TariffStatus.INCOMPLETE)


def _tariff_signature(tariff: ChargingTariff) -> tuple[object, ...]:
    return (
        tariff.currency,
        tariff.price_per_kwh,
        tariff.session_fee,
        tariff.price_per_minute,
        tariff.vat_percentage,
    )


def _parse_tariff_record(record: dict[str, object], now: datetime) -> ChargingTariff:
    """Parse one AFIR (OCPI-style) tariff, preserving unresolved price conditions."""
    currency = _required_string(record.get("currency"), "tariff currency")
    if len(currency) != 3 or not currency.isalpha():
        raise InvalidProviderResponseError("AFIR returned an invalid tariff currency")
    currency = currency.upper()

    tax_included = record.get("taxIncluded")
    if tax_included not in ("YES", "NO", "N/A"):
        raise InvalidProviderResponseError("AFIR returned invalid tax information")

    tariff_id = _optional_string(record.get("id"), "tariff id")
    tariff_type = _optional_string(record.get("type"), "tariff type")
    description = _tariff_description(record.get("tariffAltText"))

    if not _tariff_is_active(record, now):
        return ChargingTariff(
            currency=currency,
            price_per_kwh=None,
            session_fee=None,
            price_per_minute=None,
            description=description,
            source=PROVIDER_DIGITRAFFIC_AFIR_FI_NAME,
            tariff_id=tariff_id,
            tariff_type=tariff_type,
            status=TariffStatus.INACTIVE,
        )

    elements = record.get("elements")
    if not isinstance(elements, list):
        raise InvalidProviderResponseError("AFIR returned invalid tariff elements")

    conditional = False
    active_components: list[dict[str, object]] = []
    for raw_element in elements:
        element = _string_mapping(raw_element, "tariff element")
        element_status = _tariff_element_status(element.get("restrictions"), now)
        if element_status is TariffStatus.CONDITIONAL:
            conditional = True
            continue
        if element_status is TariffStatus.INACTIVE:
            continue
        for raw_component in _list_field(element, "priceComponents"):
            active_components.append(
                _string_mapping(raw_component, "tariff price component")
            )

    if conditional and not active_components:
        status = TariffStatus.CONDITIONAL
        price_per_kwh = session_fee = price_per_minute = vat_percentage = None
    elif not active_components:
        status = TariffStatus.INACTIVE if elements else TariffStatus.INCOMPLETE
        price_per_kwh = session_fee = price_per_minute = vat_percentage = None
    else:
        price_per_kwh, vat_percentage = _unique_component_price(
            active_components, "ENERGY", tax_included
        )
        session_fee, _ = _unique_component_price(
            active_components, "FLAT", tax_included
        )
        price_per_minute = _unique_time_price(active_components, tax_included)
        if conditional:
            status = TariffStatus.CONDITIONAL
            price_per_kwh = session_fee = price_per_minute = vat_percentage = None
        else:
            status = (
                TariffStatus.AVAILABLE
                if price_per_kwh is not None
                else TariffStatus.INCOMPLETE
            )

    return ChargingTariff(
        currency=currency,
        price_per_kwh=price_per_kwh,
        session_fee=session_fee,
        price_per_minute=price_per_minute,
        description=description,
        source=PROVIDER_DIGITRAFFIC_AFIR_FI_NAME,
        vat_percentage=vat_percentage,
        tariff_id=tariff_id,
        tariff_type=tariff_type,
        status=status,
    )


def _tariff_is_active(record: dict[str, object], now: datetime) -> bool:
    start = record.get("startDateTime")
    end = record.get("endDateTime")
    if isinstance(start, str) and _parse_datetime(start) > now:
        return False
    if isinstance(end, str) and _parse_datetime(end) < now:
        return False
    return True


def _tariff_element_status(restrictions: object, now: datetime) -> TariffStatus:
    """Evaluate date-only restrictions and keep usage-dependent rates unknown."""
    if restrictions is None:
        return TariffStatus.AVAILABLE
    fields = _string_mapping(restrictions, "tariff restrictions")
    local_date = now.date()
    start_date = fields.get("startDate")
    end_date = fields.get("endDate")
    if isinstance(start_date, str) and local_date < _restriction_date(
        start_date, "start date"
    ):
        return TariffStatus.INACTIVE
    if isinstance(end_date, str) and local_date >= _restriction_date(
        end_date, "end date"
    ):
        return TariffStatus.INACTIVE
    if any(
        value is not None
        for key, value in fields.items()
        if key not in ("startDate", "endDate")
    ):
        return TariffStatus.CONDITIONAL
    return TariffStatus.AVAILABLE


def _restriction_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise InvalidProviderResponseError(
            f"AFIR returned an invalid tariff restriction {field}"
        ) from err


def _unique_component_price(
    components: list[dict[str, object]],
    component_type: str,
    tax_included: object,
) -> tuple[Decimal | None, Decimal | None]:
    matching = [
        component for component in components if component.get("type") == component_type
    ]
    if not matching:
        return None, None
    prices = [_gross_component_price(component, tax_included) for component in matching]
    if any(price is None for price, _ in prices):
        return None, None
    price_values = {price for price, _ in prices}
    vat_values = {vat for _, vat in prices}
    return (
        next(iter(price_values)) if len(price_values) == 1 else None,
        next(iter(vat_values)) if len(vat_values) == 1 else None,
    )


def _unique_time_price(
    components: list[dict[str, object]], tax_included: object
) -> Decimal | None:
    prices: set[Decimal] = set()
    for component in components:
        if component.get("type") != "TIME":
            continue
        step_size = component.get("stepSize")
        if (
            not isinstance(step_size, (int, float))
            or isinstance(step_size, bool)
            or step_size <= 0
        ):
            return None
        price, _ = _gross_component_price(component, tax_included)
        if price is None:
            return None
        prices.add(price / Decimal(str(step_size)) * Decimal(60))
    return next(iter(prices)) if len(prices) == 1 else None


def _gross_component_price(
    component: dict[str, object], tax_included: object
) -> tuple[Decimal | None, Decimal | None]:
    price = _decimal_price(component.get("price"), "price component price")
    raw_vat = component.get("vat")
    vat = (
        _decimal_price(raw_vat, "price component VAT") if raw_vat is not None else None
    )
    if tax_included in ("YES", "N/A"):
        return price, vat
    if vat is not None:
        return price * (Decimal(1) + vat / Decimal(100)), vat
    return None, vat


def _decimal_price(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidProviderResponseError(f"AFIR returned an invalid {field}")
    try:
        price = Decimal(str(value))
    except InvalidOperation as err:
        raise InvalidProviderResponseError(f"AFIR returned an invalid {field}") from err
    if not price.is_finite():
        raise InvalidProviderResponseError(f"AFIR returned an invalid {field}")
    return price


def _tariff_description(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str) and text:
                return text
    return None


def _evse_status(value: object) -> EVSEStatus:
    if not isinstance(value, str):
        return EVSEStatus.UNKNOWN
    return _EVSE_STATUS_MAP.get(value.casefold(), EVSEStatus.UNKNOWN)


def _optional_nonnegative_power(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidProviderResponseError("AFIR returned an invalid connector power")
    watts = float(value)
    if not math.isfinite(watts) or watts < 0:
        raise InvalidProviderResponseError("AFIR returned an invalid connector power")
    return watts / 1000


def _format_address(value: object) -> str | None:
    if value is None:
        return None
    address = _string_mapping(value, "address")
    street = _optional_string(address.get("street"), "street")
    postal_code = _optional_string(address.get("postalCode"), "postal code")
    city = _optional_string(address.get("city"), "city")
    locality = " ".join(part for part in (postal_code, city) if part)
    return ", ".join(part for part in (street, locality) if part) or None


def _geometry_coordinates(value: object, field: str) -> tuple[float, float]:
    geometry = _string_mapping(value, field)
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        raise InvalidProviderResponseError(f"AFIR returned an invalid {field}")
    longitude, latitude = coordinates
    if (
        isinstance(latitude, bool)
        or isinstance(longitude, bool)
        or not isinstance(latitude, (int, float))
        or not isinstance(longitude, (int, float))
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        raise InvalidProviderResponseError(f"AFIR returned an invalid {field}")
    return float(latitude), float(longitude)


def _parse_datetime(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise InvalidProviderResponseError(
            "AFIR returned an invalid timestamp"
        ) from err
    if timestamp.tzinfo is None:
        raise InvalidProviderResponseError("AFIR returned a timezone-naive timestamp")
    return timestamp.astimezone(UTC)


def _optional_datetime(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidProviderResponseError(f"AFIR returned an invalid {field}")
    return _parse_datetime(value)


def _string_mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise InvalidProviderResponseError(f"AFIR returned invalid {field}")
    return cast(dict[str, object], value)


def _list_field(mapping: dict[str, object], field: str) -> list[object]:
    value = mapping.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidProviderResponseError(f"AFIR returned an invalid {field} list")
    return cast(list[object], value)


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidProviderResponseError(f"AFIR returned an invalid {field}")
    return value.strip()


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidProviderResponseError(f"AFIR returned an invalid {field}")
    return value.strip() or None
