"""Async client for the Swiss Federal Office of Energy's ich-tanke-strom OCPI API."""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import (
    COUNTRY_CH,
    DOMAIN,
    MAX_MAX_STATIONS,
    MAX_SEARCH_RADIUS_M,
    MIN_MAX_STATIONS,
    MIN_SEARCH_RADIUS_M,
    PROVIDER_ICH_TANKE_STROM_CH,
    PROVIDER_ICH_TANKE_STROM_CH_NAME,
)
from ..geography import distance_m
from ..models import (
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
from .base import (
    BaseChargingProvider,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
    retry_after_seconds,
)

_API_BASE_URL = "https://api.emobility-prod.prometheon.bfe.admin.ch/ocpi"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)
_LOCATION_PAGE_LIMIT = "1000"
_STATUS_PAGE_LIMIT = "5000"
# ponytail: the catalogue measured 2 pages of 1000 and the status set 1 page of
# 5000 on 2026-11-03; this guard only stops a misbehaving Link chain.
_MAX_PAGES = 50
_OCPI_SUCCESS = 1000
# ponytail: the location's ISO 3166-1 alpha-3 country, not the operator's
# country_code; a FR operator legitimately serves CHE sites.
_LOCATION_COUNTRY = "CHE"
_REQUEST_LIMITER_DATA_KEY = "switzerland_request_limiter"
# ponytail: 1.1s pacing stays under the documented example of 60 requests per
# minute per client; adjust if SFOE publishes the final limit.
_MIN_REQUEST_INTERVAL_SECONDS = 1.1
# ponytail: locations and tariffs refresh "several times a day" upstream; live
# availability comes from /status deltas on every poll, never from the cache.
_CATALOG_CACHE_SECONDS = 15 * 60
# ponytail: overlap on delta polls so a late CPO push is not missed, as the
# SFOE docs recommend for date_from synchronisation.
_STATUS_DELTA_OVERLAP = timedelta(minutes=5)
_INITIAL_STATUS_DATE_FROM = "2000-01-01T00:00:00Z"
_NEXT_LINK_PATTERN = re.compile(r"<([^>]+)>\s*;\s*rel=\"next\"")
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

StatusKey = tuple[str, str, str, str]
StatusRecord = tuple[EVSEStatus, datetime | None]


def is_valid_switzerland_search_radius(radius_m: int) -> bool:
    """Return whether a search radius is within the supported bounds."""
    return MIN_SEARCH_RADIUS_M <= radius_m <= MAX_SEARCH_RADIUS_M


class SwitzerlandRequestLimiter:
    """Pace anonymous OCPI requests shared by this Home Assistant instance."""

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


def _shared_request_limiter(hass: HomeAssistant) -> SwitzerlandRequestLimiter:
    """Return the request limiter shared by Swiss clients in this HA instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    limiter = domain_data.get(_REQUEST_LIMITER_DATA_KEY)
    if limiter is None:
        limiter = SwitzerlandRequestLimiter()
        domain_data[_REQUEST_LIMITER_DATA_KEY] = limiter
    if not isinstance(limiter, SwitzerlandRequestLimiter):
        raise RuntimeError("CurbCharge Switzerland request limiter has an invalid type")
    return limiter


def create_switzerland_provider(
    session: aiohttp.ClientSession,
    hass: HomeAssistant | None = None,
) -> SwitzerlandChargingProvider:
    """Create the Swiss ich-tanke-strom provider, sharing a limiter where possible."""
    limiter = (
        _shared_request_limiter(hass)
        if hass is not None
        else SwitzerlandRequestLimiter()
    )
    return SwitzerlandChargingProvider(session, limiter)


class SwitzerlandChargingProvider(BaseChargingProvider):
    """Fetch and normalize SFOE ich-tanke-strom OCPI data for Switzerland."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        request_limiter: SwitzerlandRequestLimiter,
    ) -> None:
        """Initialize the client using Home Assistant's shared session."""
        self._session = session
        self._request_limiter = request_limiter
        self._catalog: (
            tuple[list[dict[str, object]], dict[str, ChargingTariff]] | None
        ) = None
        self._catalog_cached_at: float | None = None
        self._statuses: dict[StatusKey, StatusRecord] = {}
        self._status_synced_at: datetime | None = None

    @property
    def provider_name(self) -> str:
        """Return the display name of the provider."""
        return PROVIDER_ICH_TANKE_STROM_CH_NAME

    @property
    def provider_id(self) -> str:
        """Return the stable Switzerland provider identifier."""
        return PROVIDER_ICH_TANKE_STROM_CH

    @property
    def country_code(self) -> str:
        """Return the country served by this provider."""
        return COUNTRY_CH

    async def async_validate_connection(
        self, latitude: float, longitude: float, radius_m: int
    ) -> None:
        """Fetch and parse one page of locations without caching it."""
        _validate_search(latitude, longitude, radius_m, MIN_MAX_STATIONS)
        payload, _ = await self._async_request("/locations", {"limit": "1"})
        for location in _envelope_list(payload, "locations"):
            _stations_from_location(location, latitude, longitude, {}, {})

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
        """Fetch all Swiss stations and select nearby and favourited ones."""
        _validate_search(latitude, longitude, radius_m, max_stations)
        if any(favourite.provider != self.provider_id for favourite in favourites):
            raise InvalidProviderResponseError(
                "A saved favourite belongs to a different provider"
            )

        locations, tariffs = await self._async_get_catalog()
        statuses = await self._async_get_statuses()

        all_stations: list[ChargingStation] = []
        for location in locations:
            all_stations.extend(
                _stations_from_location(
                    location, latitude, longitude, statuses, tariffs
                )
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
    ) -> tuple[list[dict[str, object]], dict[str, ChargingTariff]]:
        """Return cached Swiss locations and tariffs, refreshing when stale."""
        now = time.monotonic()
        if (
            self._catalog is not None
            and self._catalog_cached_at is not None
            and now - self._catalog_cached_at < _CATALOG_CACHE_SECONDS
        ):
            return self._catalog

        locations: list[dict[str, object]] = []
        for raw_location in await self._async_paginated(
            "/locations", {"limit": _LOCATION_PAGE_LIMIT}
        ):
            location = _string_mapping(raw_location, "location")
            country = _optional_string(location.get("country"), "location country")
            if country is None or country.upper() != _LOCATION_COUNTRY:
                continue
            locations.append(location)

        now_utc = datetime.now(UTC)
        tariffs: dict[str, ChargingTariff] = {}
        for raw_tariff in await self._async_paginated(
            "/tariffs", {"limit": _LOCATION_PAGE_LIMIT}
        ):
            record = _string_mapping(raw_tariff, "tariff")
            tariff_id = _required_string(record.get("id"), "tariff id")
            tariffs[tariff_id] = _parse_tariff_record(record, now_utc)

        catalog = (locations, tariffs)
        self._catalog = catalog
        self._catalog_cached_at = time.monotonic()
        return catalog

    async def _async_get_statuses(self) -> dict[StatusKey, StatusRecord]:
        """Merge EVSE status records received since the last successful poll."""
        if self._status_synced_at is None:
            date_from = _INITIAL_STATUS_DATE_FROM
        else:
            date_from = _format_datetime(self._status_synced_at - _STATUS_DELTA_OVERLAP)
        requested_at = datetime.now(UTC)
        raw_records = await self._async_paginated(
            "/status", {"date_from": date_from, "limit": _STATUS_PAGE_LIMIT}
        )
        for raw_record in raw_records:
            record = _string_mapping(raw_record, "status")
            key = (
                _required_string(record.get("country_code"), "status country code"),
                _required_string(record.get("party_id"), "status party id"),
                _required_string(record.get("location_id"), "status location id"),
                _required_string(record.get("evse_uid"), "status EVSE uid"),
            )
            status = _evse_status(record.get("status"))
            last_updated = _optional_datetime(
                record.get("last_updated"), "status last_updated"
            )
            previous = self._statuses.get(key)
            if (
                previous is not None
                and previous[1] is not None
                and last_updated is not None
                and last_updated < previous[1]
            ):
                continue
            self._statuses[key] = (status, last_updated)
        self._status_synced_at = requested_at
        return self._statuses

    async def _async_paginated(self, path: str, params: dict[str, str]) -> list[object]:
        """Follow OCPI Link headers, bounded against a looping chain."""
        collected: list[object] = []
        url: str | None = f"{_API_BASE_URL}{path}"
        query: dict[str, str] | None = params
        seen_urls: set[str] = set()
        for _ in range(_MAX_PAGES):
            assert url is not None
            payload, next_url = await self._async_request_url(url, query)
            collected.extend(_envelope_list(payload, path.strip("/")))
            if next_url is None:
                return collected
            if next_url in seen_urls or not next_url.startswith(_API_BASE_URL):
                raise InvalidProviderResponseError(
                    "ich-tanke-strom returned an invalid pagination link"
                )
            seen_urls.add(next_url)
            url, query = next_url, None
        raise InvalidProviderResponseError(
            "ich-tanke-strom pagination exceeded the expected page bound"
        )

    async def _async_request(
        self, path: str, params: dict[str, str]
    ) -> tuple[object, str | None]:
        return await self._async_request_url(f"{_API_BASE_URL}{path}", params)

    async def _async_request_url(
        self, url: str, params: dict[str, str] | None
    ) -> tuple[object, str | None]:
        """Send one anonymous, rate-limited GET and return its JSON and next link."""
        await self._request_limiter.async_wait()
        try:
            async with self._session.get(
                url,
                params=params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "ha_curbcharge",
                },
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                if response.status == 429:
                    retry_after = retry_after_seconds(
                        response.headers.get("Retry-After")
                    )
                    self._request_limiter.defer_for_retry_after(retry_after)
                    raise ProviderRateLimitError(
                        "ich-tanke-strom rate-limited the request", retry_after
                    )
                if 500 <= response.status < 600:
                    raise ProviderConnectionError(
                        "ich-tanke-strom returned a temporary server error"
                    )
                # ponytail: open data without credentials; any other non-2xx is
                # a connection problem, not an authentication failure.
                if not 200 <= response.status < 300:
                    raise ProviderConnectionError(
                        "ich-tanke-strom returned an unsuccessful response"
                    )
                body = await response.read()
                link = response.headers.get("Link")
        except (ProviderConnectionError, ProviderRateLimitError):
            raise
        except (TimeoutError, aiohttp.ClientError, OSError):
            raise ProviderConnectionError(
                "Could not connect to the Switzerland charging-data provider"
            ) from None

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise InvalidProviderResponseError(
                "ich-tanke-strom returned invalid JSON"
            ) from err
        return payload, _next_link(link)


def _next_link(link: object) -> str | None:
    if not isinstance(link, str):
        return None
    match = _NEXT_LINK_PATTERN.search(link)
    return match.group(1) if match else None


def _envelope_list(payload: object, field: str) -> list[object]:
    """Unwrap the OCPI response envelope and require a successful status code."""
    envelope = _string_mapping(payload, f"{field} response")
    status_code = envelope.get("status_code")
    if status_code != _OCPI_SUCCESS:
        raise InvalidProviderResponseError(
            "ich-tanke-strom returned an unsuccessful OCPI status code"
        )
    return _list_field(envelope, "data")


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
        raise UnsupportedLocationError("The Switzerland search location is invalid")
    if not is_valid_switzerland_search_radius(radius_m):
        raise UnsupportedLocationError("The Switzerland search radius is unsupported")
    if not MIN_MAX_STATIONS <= limit <= MAX_MAX_STATIONS:
        raise UnsupportedLocationError("The Switzerland station limit is unsupported")


def _stations_from_location(
    raw_location: object,
    latitude: float,
    longitude: float,
    statuses: dict[StatusKey, StatusRecord],
    tariffs: dict[str, ChargingTariff],
) -> list[ChargingStation]:
    """Build one ChargingStation per EVSE at an OCPI location."""
    location_data = _string_mapping(raw_location, "location")
    country = _optional_string(location_data.get("country"), "location country")
    if country is None or country.upper() != _LOCATION_COUNTRY:
        return []

    country_code = _required_string(
        location_data.get("country_code"), "location country code"
    )
    party_id = _required_string(location_data.get("party_id"), "location party id")
    location_id = _required_string(location_data.get("id"), "location id")
    coordinates = _coordinates(location_data.get("coordinates"))
    name = _optional_string(location_data.get("name"), "location name")
    address = _format_address(location_data)
    operator = location_data.get("operator")
    operator_name = (
        _optional_string(
            _string_mapping(operator, "operator").get("name"), "operator name"
        )
        if operator is not None
        else None
    )
    operator_id = f"{country_code}*{party_id}"
    location_updated = _optional_datetime(
        location_data.get("last_updated"), "location last_updated"
    )
    location_key = f"{country_code}*{party_id}*{location_id}"
    base_name = name or address or location_key

    location = ChargingLocation(
        provider_id=location_key,
        provider=PROVIDER_ICH_TANKE_STROM_CH,
        provider_name=PROVIDER_ICH_TANKE_STROM_CH_NAME,
        country_code=COUNTRY_CH,
        name=base_name,
        latitude=coordinates[0],
        longitude=coordinates[1],
        address=address,
        operator=operator_name,
        last_updated=location_updated,
    )

    stations: list[ChargingStation] = []
    for raw_evse in _list_field(location_data, "evses"):
        evse = _string_mapping(raw_evse, "EVSE")
        evse_uid = _required_string(evse.get("uid"), "EVSE uid")
        evse_id = _optional_string(evse.get("evse_id"), "EVSE id") or evse_uid
        station_id = f"{location_key}*{evse_uid}"
        # ponytail: the embedded evse.status refreshes only a few times a day
        # upstream; live availability comes solely from the /status deltas.
        status, status_updated_at = statuses.get(
            (country_code, party_id, location_id, evse_uid),
            (EVSEStatus.UNKNOWN, None),
        )
        if status is EVSEStatus.REMOVED:
            continue

        physical_reference = _optional_string(
            evse.get("physical_reference"), "EVSE physical reference"
        )
        station_name = (
            base_name
            if physical_reference is None
            else f"{base_name} ({physical_reference})"
        )
        evse_updated = _optional_datetime(evse.get("last_updated"), "EVSE last_updated")
        connectors = _connectors_from_evse(
            station_id, _list_field(evse, "connectors"), tariffs
        )
        availability_reported = status_updated_at is not None
        last_updated_source: ProviderTimestampSource | None
        if availability_reported:
            last_updated = status_updated_at
            last_updated_source = ProviderTimestampSource.SWITZERLAND_EVSE_STATUS
        else:
            last_updated = evse_updated
            last_updated_source = (
                ProviderTimestampSource.SWITZERLAND_STATIC_FALLBACK
                if evse_updated is not None
                else None
            )

        station = ChargingStation(
            provider_id=station_id,
            name=station_name,
            latitude=coordinates[0],
            longitude=coordinates[1],
            address=address,
            operator=operator_name,
            distance_m=distance_m(latitude, longitude, coordinates[0], coordinates[1]),
            connectors=connectors,
            tariff=None,
            last_updated=last_updated,
            provider=PROVIDER_ICH_TANKE_STROM_CH,
            availability_reported=availability_reported,
            evse_status=status,
            is_open=None,
            country_code=COUNTRY_CH,
            location=location,
            provider_name=PROVIDER_ICH_TANKE_STROM_CH_NAME,
            evse_id=evse_id,
            connector_count_reported=True,
            last_updated_source=last_updated_source,
            operator_id=operator_id,
        )
        stations.append(_with_station_tariff(station))
    return stations


def _connectors_from_evse(
    station_id: str,
    raw_connectors: list[object],
    tariffs: dict[str, ChargingTariff],
) -> tuple[ChargingConnector, ...]:
    connectors: list[ChargingConnector] = []
    for index, raw_connector in enumerate(raw_connectors):
        connector = _string_mapping(raw_connector, "connector")
        standard = _optional_string(connector.get("standard"), "connector standard")
        connector_format = _optional_string(connector.get("format"), "connector format")
        power_type = _optional_string(
            connector.get("power_type"), "connector power type"
        )
        max_power_kw = _optional_nonnegative_power(connector.get("max_electric_power"))
        raw_tariff_ids = connector.get("tariff_ids")
        if raw_tariff_ids is not None and not isinstance(raw_tariff_ids, list):
            raise InvalidProviderResponseError(
                "ich-tanke-strom returned an invalid connector tariff_ids list"
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
                provider_connector_id=f"{station_id}:connector:{index}",
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
    """Parse one OCPI tariff, preserving unresolved price conditions."""
    currency = _required_string(record.get("currency"), "tariff currency")
    if len(currency) != 3 or not currency.isalpha():
        raise InvalidProviderResponseError(
            "ich-tanke-strom returned an invalid tariff currency"
        )
    currency = currency.upper()

    tax_included = record.get("tax_included")
    if tax_included not in ("YES", "NO", "N/A"):
        raise InvalidProviderResponseError(
            "ich-tanke-strom returned invalid tax information"
        )

    tariff_id = _optional_string(record.get("id"), "tariff id")
    tariff_type = _optional_string(record.get("type"), "tariff type")
    description = _tariff_description(record.get("tariff_alt_text"))

    if not _tariff_is_active(record, now):
        return ChargingTariff(
            currency=currency,
            price_per_kwh=None,
            session_fee=None,
            price_per_minute=None,
            description=description,
            source=PROVIDER_ICH_TANKE_STROM_CH_NAME,
            tariff_id=tariff_id,
            tariff_type=tariff_type,
            status=TariffStatus.INACTIVE,
        )

    elements = record.get("elements")
    if not isinstance(elements, list):
        raise InvalidProviderResponseError(
            "ich-tanke-strom returned invalid tariff elements"
        )

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
        for raw_component in _list_field(element, "price_components"):
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
        source=PROVIDER_ICH_TANKE_STROM_CH_NAME,
        vat_percentage=vat_percentage,
        tariff_id=tariff_id,
        tariff_type=tariff_type,
        status=status,
    )


def _tariff_is_active(record: dict[str, object], now: datetime) -> bool:
    start = record.get("start_date_time")
    end = record.get("end_date_time")
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
    start_date = fields.get("start_date")
    end_date = fields.get("end_date")
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
        if key not in ("start_date", "end_date")
    ):
        return TariffStatus.CONDITIONAL
    return TariffStatus.AVAILABLE


def _restriction_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise InvalidProviderResponseError(
            f"ich-tanke-strom returned an invalid tariff restriction {field}"
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
        step_size = component.get("step_size")
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
        raise InvalidProviderResponseError(
            f"ich-tanke-strom returned an invalid {field}"
        )
    try:
        price = Decimal(str(value))
    except InvalidOperation as err:
        raise InvalidProviderResponseError(
            f"ich-tanke-strom returned an invalid {field}"
        ) from err
    if not price.is_finite():
        raise InvalidProviderResponseError(
            f"ich-tanke-strom returned an invalid {field}"
        )
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
        raise InvalidProviderResponseError(
            "ich-tanke-strom returned an invalid connector power"
        )
    watts = float(value)
    if not math.isfinite(watts) or watts < 0:
        raise InvalidProviderResponseError(
            "ich-tanke-strom returned an invalid connector power"
        )
    return watts / 1000


def _format_address(location: dict[str, object]) -> str | None:
    # ponytail: live records carry tab characters and shifted address fields;
    # whitespace-normalise and treat the text as display-only.
    street = _clean_text(location.get("address"), "address")
    postal_code = _clean_text(location.get("postal_code"), "postal code")
    city = _clean_text(location.get("city"), "city")
    locality = " ".join(part for part in (postal_code, city) if part)
    return ", ".join(part for part in (street, locality) if part) or None


def _clean_text(value: object, field: str) -> str | None:
    text = _optional_string(value, field)
    if text is None:
        return None
    return " ".join(text.split()) or None


def _coordinates(value: object) -> tuple[float, float]:
    mapping = _string_mapping(value, "location coordinates")
    latitude = _coordinate(mapping.get("latitude"), 90)
    longitude = _coordinate(mapping.get("longitude"), 180)
    return latitude, longitude


def _coordinate(value: object, bound: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidProviderResponseError(
            "ich-tanke-strom returned invalid location coordinates"
        )
    try:
        number = float(value)
    except ValueError as err:
        raise InvalidProviderResponseError(
            "ich-tanke-strom returned invalid location coordinates"
        ) from err
    if not math.isfinite(number) or not -bound <= number <= bound:
        raise InvalidProviderResponseError(
            "ich-tanke-strom returned invalid location coordinates"
        )
    return number


def _parse_datetime(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise InvalidProviderResponseError(
            "ich-tanke-strom returned an invalid timestamp"
        ) from err
    if timestamp.tzinfo is None:
        raise InvalidProviderResponseError(
            "ich-tanke-strom returned a timezone-naive timestamp"
        )
    return timestamp.astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _optional_datetime(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidProviderResponseError(
            f"ich-tanke-strom returned an invalid {field}"
        )
    return _parse_datetime(value)


def _string_mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise InvalidProviderResponseError(f"ich-tanke-strom returned invalid {field}")
    return cast(dict[str, object], value)


def _list_field(mapping: dict[str, object], field: str) -> list[object]:
    value = mapping.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvalidProviderResponseError(
            f"ich-tanke-strom returned an invalid {field} list"
        )
    return cast(list[object], value)


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidProviderResponseError(
            f"ich-tanke-strom returned an invalid {field}"
        )
    return value.strip()


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidProviderResponseError(
            f"ich-tanke-strom returned an invalid {field}"
        )
    return value.strip() or None
