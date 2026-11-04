"""Via Lietuva's public Lithuanian charging-station OCPI feed."""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import (
    COUNTRY_LT,
    DOMAIN,
    MAX_MAX_STATIONS,
    MAX_SEARCH_RADIUS_M,
    MIN_MAX_STATIONS,
    MIN_SEARCH_RADIUS_M,
    PROVIDER_VIA_LIETUVA_LT,
    PROVIDER_VIA_LIETUVA_LT_NAME,
)
from ..geography import distance_m
from ..location import valid_coordinates
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
    ChargingProviderError,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
    retry_after_seconds,
)

_API_BASE_URL = "https://ev.vialietuva.lt/ocpi/2.3.0"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
_SNAPSHOT_TIMEOUT_SECONDS = 180
_SNAPSHOT_DATA_KEY = "lithuania_snapshot"
_LOCATION_PAGE_SIZE = 1000
_MAX_LOCATION_PAGES = 20
_MAX_REFRESH_BYTES = 16 * 1024 * 1024
_MIN_SNAPSHOT_INTERVAL_SECONDS = 5 * 60
_TARIFF_CACHE_SECONDS = 15 * 60
_MIN_REQUEST_INTERVAL_SECONDS = 1.0
_MAX_COOLDOWN_SECONDS = 60 * 60
_CHUNK_SIZE = 64 * 1024
_EVSE_STATUS_MAP = {
    "AVAILABLE": EVSEStatus.AVAILABLE,
    "BLOCKED": EVSEStatus.BLOCKED,
    "CHARGING": EVSEStatus.CHARGING,
    "INOPERATIVE": EVSEStatus.INOPERATIVE,
    "OUTOFORDER": EVSEStatus.OUT_OF_ORDER,
    "PLANNED": EVSEStatus.PLANNED,
    "REMOVED": EVSEStatus.REMOVED,
    "RESERVED": EVSEStatus.RESERVED,
}
_CONNECTOR_TYPE_MAP = {
    "CHADEMO": ConnectorTypeId.CHADEMO,
    "IEC_62196_T2": ConnectorTypeId.TYPE_2,
    "IEC_62196_T2_COMBO": ConnectorTypeId.CCS_COMBO_2,
}
_TARIFF_STATUS_PRIORITY = (
    TariffStatus.CONDITIONAL,
    TariffStatus.INACTIVE,
    TariffStatus.MISSING,
    TariffStatus.AMBIGUOUS,
    TariffStatus.INCOMPLETE,
)

_FailureKind = Literal["connection", "invalid_response", "rate_limit"]
_LocationKey = tuple[str, str, str]
_TariffKey = tuple[str, str, str]


@dataclass(slots=True)
class _RefreshBudget:
    """Count decompressed response bytes across one atomic country refresh."""

    maximum: int = _MAX_REFRESH_BYTES
    used: int = 0

    async def read(self, response: aiohttp.ClientResponse) -> bytes:
        body = bytearray()
        async for chunk in response.content.iter_chunked(_CHUNK_SIZE):
            if self.used + len(body) + len(chunk) > self.maximum:
                raise InvalidProviderResponseError(
                    "Via Lietuva refresh exceeded its response-size budget"
                )
            body.extend(chunk)
        self.used += len(body)
        return bytes(body)


class LithuaniaSnapshot:
    """Share the complete parsed catalogue and its request limits in one HA."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._locations: tuple[dict[str, object], ...] | None = None
        self._tariffs: tuple[dict[str, object], ...] | None = None
        self._locations_cached_at: float | None = None
        self._tariffs_cached_at: float | None = None
        self._next_location_request_at = 0.0
        self._retry_at = 0.0
        self._last_request_at: float | None = None
        self._cooldown_seconds = _MIN_SNAPSHOT_INTERVAL_SECONDS
        self._failure: tuple[_FailureKind, str, int | None] | None = None

    async def async_get(
        self, session: aiohttp.ClientSession
    ) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
        """Return a complete cached view or refresh it atomically."""
        async with self._lock:
            now = time.monotonic()
            locations_due = (
                self._locations is None
                or self._locations_cached_at is None
                or now - self._locations_cached_at >= _MIN_SNAPSHOT_INTERVAL_SECONDS
            )
            tariffs_due = (
                self._tariffs is None
                or self._tariffs_cached_at is None
                or now - self._tariffs_cached_at >= _TARIFF_CACHE_SECONDS
            )
            if not locations_due and not tariffs_due:
                if self._locations is None or self._tariffs is None:
                    raise ProviderConnectionError("Via Lietuva cache is incomplete")
                return self._locations, self._tariffs

            if now < self._retry_at or (
                locations_due and now < self._next_location_request_at
            ):
                self._raise_previous_failure()

            self._failure = (
                "connection",
                "Via Lietuva refresh did not complete",
                None,
            )
            if locations_due:
                self._next_location_request_at = now + _MIN_SNAPSHOT_INTERVAL_SECONDS
            locations = self._locations
            tariffs = self._tariffs
            locations_cached_at = self._locations_cached_at
            tariffs_cached_at = self._tariffs_cached_at
            budget = _RefreshBudget()

            try:
                async with asyncio.timeout(_SNAPSHOT_TIMEOUT_SECONDS):
                    if locations_due:
                        locations = await self._async_get_locations(session, budget)
                        locations_cached_at = time.monotonic()
                    if tariffs_due:
                        tariffs = await self._async_get_tariffs(session, budget)
                        tariffs_cached_at = time.monotonic()
            except asyncio.CancelledError:
                raise
            except ProviderRateLimitError as err:
                self._failure = ("rate_limit", str(err), err.retry_after)
                raise
            except InvalidProviderResponseError as err:
                self._failure = ("invalid_response", str(err), None)
                raise
            except ChargingProviderError as err:
                self._failure = ("connection", str(err), None)
                raise
            except TimeoutError:
                error = ProviderConnectionError(
                    "Via Lietuva refresh timed out before completion"
                )
                self._failure = ("connection", str(error), None)
                raise error from None
            finally:
                if self._failure is not None:
                    self._retry_at = max(
                        self._retry_at,
                        time.monotonic() + _MIN_SNAPSHOT_INTERVAL_SECONDS,
                    )

            if locations is None or tariffs is None:
                raise ProviderConnectionError(
                    "Via Lietuva refresh did not produce a complete snapshot"
                )

            self._locations = locations
            self._tariffs = tariffs
            self._locations_cached_at = locations_cached_at
            self._tariffs_cached_at = tariffs_cached_at
            self._failure = None
            self._retry_at = 0.0
            self._cooldown_seconds = _MIN_SNAPSHOT_INTERVAL_SECONDS
            return locations, tariffs

    def _raise_previous_failure(self) -> None:
        """Repeat the last explicit failure instead of serving stale success."""
        if self._failure is None:
            raise ProviderConnectionError("Via Lietuva refresh is still in progress")
        kind, message, retry_after = self._failure
        if kind == "rate_limit":
            raise ProviderRateLimitError(message, retry_after)
        if kind == "invalid_response":
            raise InvalidProviderResponseError(message)
        raise ProviderConnectionError(message)

    async def _async_get_locations(
        self, session: aiohttp.ClientSession, budget: _RefreshBudget
    ) -> tuple[dict[str, object], ...]:
        locations: list[dict[str, object]] = []
        seen_ids: set[_LocationKey] = set()
        total_count: int | None = None
        page_count: int | None = None

        for page_index in range(_MAX_LOCATION_PAGES):
            offset = page_index * _LOCATION_PAGE_SIZE
            payload, headers = await self._async_request(
                session,
                "/locations",
                {"limit": str(_LOCATION_PAGE_SIZE), "offset": str(offset)},
                budget,
            )
            current_total = _total_count(headers.get("X-Total-Count"))
            if total_count is None:
                total_count = current_total
                page_count = max(1, math.ceil(total_count / _LOCATION_PAGE_SIZE))
                if page_count > _MAX_LOCATION_PAGES:
                    raise InvalidProviderResponseError(
                        "Via Lietuva catalogue exceeds the supported page bound"
                    )
            elif current_total != total_count:
                raise InvalidProviderResponseError(
                    "Via Lietuva total changed during pagination"
                )

            records = _response_data(payload, "locations")
            if total_count == 0 and records:
                raise InvalidProviderResponseError(
                    "Via Lietuva returned locations with a zero total"
                )
            if len(records) > _LOCATION_PAGE_SIZE:
                raise InvalidProviderResponseError(
                    "Via Lietuva locations page exceeded its requested size"
                )
            for raw_location in records:
                location = _mapping(raw_location, "location")
                key = _location_key(location)
                if key in seen_ids:
                    raise InvalidProviderResponseError(
                        "Via Lietuva repeated a location during pagination"
                    )
                seen_ids.add(key)

                published = location.get("publish")
                if not isinstance(published, bool):
                    raise InvalidProviderResponseError(
                        "Via Lietuva location has an invalid publish flag"
                    )
                if not published:
                    continue
                country = _required_string(location.get("country"), "location country")
                if country.upper() == "LTU":
                    locations.append(location)

            if page_count is not None and page_index + 1 >= page_count:
                return tuple(locations)

        raise InvalidProviderResponseError(
            "Via Lietuva location pagination exceeded the supported page bound"
        )

    async def _async_get_tariffs(
        self, session: aiohttp.ClientSession, budget: _RefreshBudget
    ) -> tuple[dict[str, object], ...]:
        payload, headers = await self._async_request(session, "/tariffs", None, budget)
        if headers.get("Link"):
            raise InvalidProviderResponseError(
                "Via Lietuva tariff response unexpectedly requires pagination"
            )
        return tuple(
            _mapping(item, "tariff") for item in _response_data(payload, "tariffs")
        )

    async def _async_request(
        self,
        session: aiohttp.ClientSession,
        path: str,
        params: dict[str, str] | None,
        budget: _RefreshBudget,
    ) -> tuple[object, Mapping[str, str]]:
        if self._last_request_at is not None:
            delay = _MIN_REQUEST_INTERVAL_SECONDS - (
                time.monotonic() - self._last_request_at
            )
            if delay > 0:
                await asyncio.sleep(delay)
        self._last_request_at = time.monotonic()

        try:
            async with session.get(
                f"{_API_BASE_URL}{path}",
                params=params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "CurbCharge",
                },
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                if response.status == 429:
                    retry_after = retry_after_seconds(
                        response.headers.get("Retry-After")
                    )
                    delay = max(
                        _MIN_SNAPSHOT_INTERVAL_SECONDS,
                        retry_after or self._cooldown_seconds,
                    )
                    self._retry_at = max(self._retry_at, time.monotonic() + delay)
                    self._cooldown_seconds = min(
                        self._cooldown_seconds * 2, _MAX_COOLDOWN_SECONDS
                    )
                    raise ProviderRateLimitError(
                        "Via Lietuva rate-limited the request", delay
                    )
                if response.status != 200:
                    raise ProviderConnectionError(
                        f"Via Lietuva request failed (HTTP {response.status})"
                    )
                body = await budget.read(response)
                response_headers = response.headers
        except ChargingProviderError:
            raise
        except (aiohttp.ClientError, OSError, TimeoutError):
            raise ProviderConnectionError(
                "Unable to fetch Via Lietuva charging data"
            ) from None

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise InvalidProviderResponseError(
                "Via Lietuva returned invalid JSON"
            ) from None
        return payload, response_headers


def _shared_snapshot(hass: HomeAssistant) -> LithuaniaSnapshot:
    data = hass.data.setdefault(DOMAIN, {})
    snapshot = data.get(_SNAPSHOT_DATA_KEY)
    if snapshot is None:
        snapshot = LithuaniaSnapshot()
        data[_SNAPSHOT_DATA_KEY] = snapshot
    if not isinstance(snapshot, LithuaniaSnapshot):
        raise RuntimeError("CurbCharge Lithuania snapshot has an invalid type")
    return snapshot


def create_lithuania_provider(
    session: aiohttp.ClientSession, hass: HomeAssistant | None = None
) -> LithuaniaChargingProvider:
    """Create the Via Lietuva client, sharing its snapshot in Home Assistant."""
    snapshot = _shared_snapshot(hass) if hass is not None else LithuaniaSnapshot()
    return LithuaniaChargingProvider(session, snapshot)


class LithuaniaChargingProvider(BaseChargingProvider):
    """Normalize public Via Lietuva OCPI 2.3.0 data for Lithuania."""

    def __init__(self, session: aiohttp.ClientSession, snapshot: LithuaniaSnapshot):
        self._session = session
        self._snapshot = snapshot

    @property
    def provider_name(self) -> str:
        return PROVIDER_VIA_LIETUVA_LT_NAME

    @property
    def provider_id(self) -> str:
        return PROVIDER_VIA_LIETUVA_LT

    @property
    def country_code(self) -> str:
        return COUNTRY_LT

    async def async_validate_connection(
        self, latitude: float, longitude: float, radius_m: int
    ) -> None:
        _validate_search(latitude, longitude, radius_m, MIN_MAX_STATIONS)
        await self._snapshot.async_get(self._session)

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
        _validate_search(latitude, longitude, radius_m, max_stations)
        if any(favourite.provider != self.provider_id for favourite in favourites):
            raise InvalidProviderResponseError(
                "A saved favourite belongs to a different provider"
            )

        locations, raw_tariffs = await self._snapshot.async_get(self._session)
        tariff_index = _index_tariffs(raw_tariffs)
        now = datetime.now(UTC)
        all_stations: list[ChargingStation] = []
        for raw_location in locations:
            all_stations.extend(
                _stations_from_location(
                    raw_location, latitude, longitude, tariff_index, now
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
        return StationDiscoveryResult(
            nearby=tuple(nearby_candidates[:max_stations]),
            favourites=tuple(
                stations_by_id[favourite.provider_id]
                for favourite in favourites
                if favourite.provider_id in stations_by_id
            ),
        )


def _validate_search(
    latitude: float, longitude: float, radius_m: int, max_stations: int
) -> None:
    if valid_coordinates(latitude, longitude) is None:
        raise UnsupportedLocationError("Via Lietuva search coordinates are invalid")
    if (
        isinstance(radius_m, bool)
        or not isinstance(radius_m, int)
        or not MIN_SEARCH_RADIUS_M <= radius_m <= MAX_SEARCH_RADIUS_M
        or isinstance(max_stations, bool)
        or not isinstance(max_stations, int)
        or not MIN_MAX_STATIONS <= max_stations <= MAX_MAX_STATIONS
    ):
        raise UnsupportedLocationError("Via Lietuva search bounds are invalid")


def _total_count(value: object) -> int:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise InvalidProviderResponseError(
            "Via Lietuva locations response has no valid total count"
        )
    return int(value)


def _response_data(payload: object, field: str) -> list[object]:
    envelope = _mapping(payload, f"{field} response")
    status_code = envelope.get("status_code")
    if (
        isinstance(status_code, bool)
        or not isinstance(status_code, int)
        or status_code != 1000
    ):
        raise InvalidProviderResponseError(
            f"Via Lietuva returned an unsuccessful OCPI {field} response"
        )
    records = envelope.get("data")
    if not isinstance(records, list):
        raise InvalidProviderResponseError(
            f"Via Lietuva {field} response has no data list"
        )
    return records


def _location_key(location: dict[str, object]) -> _LocationKey:
    country = _country_code(location.get("country_code"), "location country_code")
    party = _identifier(location.get("party_id"), "location party_id")
    location_id = _identifier(location.get("id"), "location id")
    return country, party, location_id


def _index_tariffs(
    tariffs: tuple[dict[str, object], ...],
) -> tuple[
    dict[_TariffKey, tuple[dict[str, object], ...]],
    dict[tuple[str, str], tuple[dict[str, object], ...]],
]:
    by_namespace: dict[_TariffKey, list[dict[str, object]]] = defaultdict(list)
    by_country_id: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for tariff in tariffs:
        country = _country_code(tariff.get("country_code"), "tariff country_code")
        party = _identifier(tariff.get("party_id"), "tariff party_id")
        tariff_id = _identifier(tariff.get("id"), "tariff id")
        by_namespace[(country, party, tariff_id)].append(tariff)
        by_country_id[(country, tariff_id)].append(tariff)
    return (
        {key: tuple(records) for key, records in by_namespace.items()},
        {key: tuple(records) for key, records in by_country_id.items()},
    )


def _stations_from_location(
    raw_location: dict[str, object],
    latitude: float,
    longitude: float,
    tariff_index: tuple[
        dict[_TariffKey, tuple[dict[str, object], ...]],
        dict[tuple[str, str], tuple[dict[str, object], ...]],
    ],
    now: datetime,
) -> list[ChargingStation]:
    location = raw_location
    location_key = _location_key(location)
    country = _required_string(location.get("country"), "location country").upper()
    if country != "LTU":
        return []
    coordinates = _coordinates(location.get("coordinates"))
    location_name = _optional_string(location.get("name"), "location name")
    location_id = location_key[2]
    base_name = location_name or _format_address(location) or location_id
    address = _format_address(location)
    operator = location.get("operator")
    operator_name = (
        _optional_string(_mapping(operator, "operator").get("name"), "operator name")
        if operator is not None
        else None
    )
    time_zone = _optional_string(location.get("time_zone"), "location time_zone")
    location_updated = _aware_datetime(
        location.get("last_updated"), "location last_updated"
    )
    location_key_string = "*".join(location_key)
    parent = ChargingLocation(
        provider_id=location_key_string,
        provider=PROVIDER_VIA_LIETUVA_LT,
        provider_name=PROVIDER_VIA_LIETUVA_LT_NAME,
        country_code=COUNTRY_LT,
        name=base_name,
        latitude=coordinates[0],
        longitude=coordinates[1],
        address=address,
        operator=operator_name,
        last_updated=location_updated,
    )
    raw_evses = location.get("evses")
    if not isinstance(raw_evses, list):
        raise InvalidProviderResponseError("Via Lietuva location has no EVSE list")

    stations: list[ChargingStation] = []
    evse_ids: set[str] = set()
    for raw_evse in raw_evses:
        evse = _mapping(raw_evse, "EVSE")
        uid = _identifier(evse.get("uid"), "EVSE uid")
        if uid in evse_ids:
            raise InvalidProviderResponseError(
                "Via Lietuva location has duplicate EVSE IDs"
            )
        evse_ids.add(uid)
        evse_status = _evse_status(evse.get("status"))
        if evse_status is EVSEStatus.REMOVED:
            continue

        raw_connectors = evse.get("connectors")
        if not isinstance(raw_connectors, list):
            raise InvalidProviderResponseError("Via Lietuva EVSE has no connectors")
        station_id = f"{location_key_string}*{uid}"
        connectors = _connectors_from_evse(
            raw_connectors,
            station_id,
            location_key[0],
            location_key[1],
            tariff_index,
            time_zone,
            now,
        )
        evse_id = _optional_string(evse.get("evse_id"), "EVSE evse_id") or uid
        physical_reference = _optional_string(
            evse.get("physical_reference"), "EVSE physical_reference"
        )
        station_name = (
            base_name
            if physical_reference is None
            else f"{base_name} ({physical_reference})"
        )
        station_updated = _aware_datetime(evse.get("last_updated"), "EVSE last_updated")
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
            last_updated=station_updated,
            provider=PROVIDER_VIA_LIETUVA_LT,
            availability_reported=evse_status is not EVSEStatus.UNKNOWN,
            country_code=COUNTRY_LT,
            location=parent,
            provider_name=PROVIDER_VIA_LIETUVA_LT_NAME,
            evse_id=evse_id,
            evse_status=evse_status,
            connector_count_reported=bool(connectors),
            last_updated_source=(
                ProviderTimestampSource.VIA_LIETUVA_RECORD
                if station_updated is not None
                else None
            ),
            operator_id=f"{location_key[0]}*{location_key[1]}",
        )
        stations.append(_with_station_tariff(station))
    return stations


def _connectors_from_evse(
    raw_connectors: list[object],
    station_id: str,
    country: str,
    party: str,
    tariff_index: tuple[
        dict[_TariffKey, tuple[dict[str, object], ...]],
        dict[tuple[str, str], tuple[dict[str, object], ...]],
    ],
    time_zone: str | None,
    now: datetime,
) -> tuple[ChargingConnector, ...]:
    connectors: list[ChargingConnector] = []
    seen_ids: set[str] = set()
    by_namespace, by_country_id = tariff_index
    for raw_connector in raw_connectors:
        connector = _mapping(raw_connector, "connector")
        connector_id = _identifier(connector.get("id"), "connector id")
        if connector_id in seen_ids:
            raise InvalidProviderResponseError(
                "Via Lietuva EVSE has duplicate connector IDs"
            )
        seen_ids.add(connector_id)
        standard = _optional_string(connector.get("standard"), "connector standard")
        power_type = _optional_string(
            connector.get("power_type"), "connector power_type"
        )
        connector_format = _optional_string(connector.get("format"), "connector format")
        raw_power = connector.get("max_electric_power")
        if raw_power is None:
            power_kw = None
        elif isinstance(raw_power, bool) or not isinstance(raw_power, (int, float)):
            raise InvalidProviderResponseError("Via Lietuva connector power is invalid")
        else:
            try:
                power_watts = float(raw_power)
            except (OverflowError, ValueError):
                raise InvalidProviderResponseError(
                    "Via Lietuva connector power is invalid"
                ) from None
            if not math.isfinite(power_watts) or power_watts < 0:
                raise InvalidProviderResponseError(
                    "Via Lietuva connector power is invalid"
                )
            power_kw = power_watts / 1000

        raw_tariff_ids = connector.get("tariff_ids")
        if raw_tariff_ids is None:
            tariff_ids: tuple[str, ...] = ()
        elif isinstance(raw_tariff_ids, list):
            tariff_ids = tuple(
                _identifier(value, "connector tariff id") for value in raw_tariff_ids
            )
            if len(set(tariff_ids)) != len(tariff_ids):
                raise InvalidProviderResponseError(
                    "Via Lietuva connector has duplicate tariff IDs"
                )
        else:
            raise InvalidProviderResponseError(
                "Via Lietuva connector tariff_ids is not a list"
            )

        resolved: list[ChargingTariff] = []
        link_statuses: list[TariffStatus] = []
        for tariff_id in tariff_ids:
            namespace_records = by_namespace.get((country, party, tariff_id), ())
            if len(namespace_records) == 1:
                raw_tariff = namespace_records[0]
            elif len(namespace_records) > 1:
                link_statuses.append(TariffStatus.AMBIGUOUS)
                continue
            else:
                # Via Lietuva currently publishes unique tariff IDs per country,
                # but some tariff party_id values differ from their location.
                candidates = by_country_id.get((country, tariff_id), ())
                if len(candidates) != 1:
                    link_statuses.append(
                        TariffStatus.AMBIGUOUS if candidates else TariffStatus.MISSING
                    )
                    continue
                raw_tariff = candidates[0]
            tariff = _parse_tariff(raw_tariff, now, time_zone)
            resolved.append(tariff)
            link_statuses.append(tariff.effective_status)

        tariff_status = _combine_tariff_statuses(tariff_ids, resolved, link_statuses)
        connectors.append(
            ChargingConnector(
                provider_connector_id=f"{station_id}:connector:{connector_id}",
                connector_type=standard,
                connector_type_id=(
                    _CONNECTOR_TYPE_MAP.get(standard) if standard is not None else None
                ),
                status=ConnectorStatus.UNKNOWN,
                max_power_kw=power_kw,
                count=1,
                available_count=None,
                connector_format=connector_format,
                power_type=power_type,
                tariff_ids=tariff_ids,
                tariffs=tuple(resolved),
                tariff_status=tariff_status,
            )
        )
    return tuple(connectors)


def _combine_tariff_statuses(
    tariff_ids: tuple[str, ...],
    resolved: list[ChargingTariff],
    statuses: list[TariffStatus],
) -> TariffStatus:
    if not tariff_ids:
        return TariffStatus.NOT_REPORTED
    if len(statuses) != len(tariff_ids):
        return TariffStatus.MISSING
    for status in _TARIFF_STATUS_PRIORITY:
        if status in statuses:
            return status
    if any(status is not TariffStatus.AVAILABLE for status in statuses):
        return TariffStatus.INCOMPLETE
    if len({_tariff_signature(tariff) for tariff in resolved}) > 1:
        return TariffStatus.AMBIGUOUS
    return TariffStatus.AVAILABLE


def _with_station_tariff(station: ChargingStation) -> ChargingStation:
    connectors = station.connectors
    if not connectors:
        return replace(station, tariff_status=TariffStatus.NOT_REPORTED)
    statuses = [
        connector.tariff_status
        or (
            TariffStatus.INCOMPLETE
            if connector.tariff_ids
            else TariffStatus.NOT_REPORTED
        )
        for connector in connectors
    ]
    all_tariffs = [tariff for connector in connectors for tariff in connector.tariffs]
    station_tariff: ChargingTariff | None = None
    connector_tariffs = [connector.tariffs for connector in connectors]
    can_aggregate = all(
        connector.tariffs
        and connector.tariff_status in (TariffStatus.AVAILABLE, TariffStatus.INCOMPLETE)
        and len({_tariff_signature(tariff) for tariff in connector.tariffs}) == 1
        for connector in connectors
    )
    if (
        can_aggregate
        and all_tariffs
        and len({_tariff_signature(tariffs[0]) for tariffs in connector_tariffs}) == 1
    ):
        ids = {tariff.tariff_id for tariff in all_tariffs}
        station_tariff = replace(
            all_tariffs[0], tariff_id=next(iter(ids)) if len(ids) == 1 else None
        )
        tariff_status = (
            TariffStatus.AVAILABLE
            if all(status is TariffStatus.AVAILABLE for status in statuses)
            else TariffStatus.INCOMPLETE
        )
    else:
        tariff_status = _combined_station_tariff_status(statuses, all_tariffs)
    return replace(station, tariff=station_tariff, tariff_status=tariff_status)


def _combined_station_tariff_status(
    statuses: list[TariffStatus], tariffs: list[ChargingTariff]
) -> TariffStatus:
    if tariffs and len({_tariff_signature(tariff) for tariff in tariffs}) > 1:
        return TariffStatus.AMBIGUOUS
    for status in _TARIFF_STATUS_PRIORITY:
        if status in statuses:
            return status
    if all(status is TariffStatus.NOT_REPORTED for status in statuses):
        return TariffStatus.NOT_REPORTED
    return TariffStatus.INCOMPLETE


def _tariff_signature(tariff: ChargingTariff) -> tuple[object, ...]:
    return (
        tariff.currency,
        tariff.price_per_kwh,
        tariff.session_fee,
        tariff.price_per_minute,
        tariff.vat_percentage,
        tariff.tariff_type,
        tariff.effective_status,
    )


def _parse_tariff(
    record: dict[str, object], now: datetime, time_zone: str | None
) -> ChargingTariff:
    currency = _required_string(record.get("currency"), "tariff currency").upper()
    if len(currency) != 3 or not currency.isalpha():
        raise InvalidProviderResponseError("Via Lietuva tariff currency is invalid")
    tariff_id = _identifier(record.get("id"), "tariff id")
    tariff_type = _optional_string(record.get("type"), "tariff type")
    tax_included = record.get("tax_included")
    if tax_included not in (None, "YES", "NO", "N/A"):
        raise InvalidProviderResponseError("Via Lietuva tax_included is invalid")

    active = _tariff_active(record, now)
    if active is TariffStatus.INACTIVE:
        return _empty_tariff(currency, tariff_id, tariff_type, TariffStatus.INACTIVE)
    if active is TariffStatus.CONDITIONAL:
        return _empty_tariff(currency, tariff_id, tariff_type, TariffStatus.CONDITIONAL)

    elements = record.get("elements")
    if not isinstance(elements, list):
        raise InvalidProviderResponseError("Via Lietuva tariff has no elements list")
    active_components: list[dict[str, object]] = []
    conditional = False
    inactive = False
    for raw_element in elements:
        element = _mapping(raw_element, "tariff element")
        restriction_status = _element_status(
            element.get("restrictions"), now, time_zone
        )
        if restriction_status is TariffStatus.CONDITIONAL:
            conditional = True
            continue
        if restriction_status is TariffStatus.INACTIVE:
            inactive = True
            continue
        components = element.get("price_components")
        if not isinstance(components, list):
            raise InvalidProviderResponseError(
                "Via Lietuva tariff element has no price_components list"
            )
        active_components.extend(
            _mapping(item, "price component") for item in components
        )

    if conditional:
        return _empty_tariff(currency, tariff_id, tariff_type, TariffStatus.CONDITIONAL)
    if not active_components:
        return _empty_tariff(
            currency,
            tariff_id,
            tariff_type,
            TariffStatus.INACTIVE if inactive else TariffStatus.INCOMPLETE,
        )

    component_values: dict[str, list[Decimal | None]] = defaultdict(list)
    vat_values: set[Decimal] = set()
    incomplete = tax_included is None
    unknown_component = False
    unsupported_parking_time = False
    for component in active_components:
        component_type = component.get("type")
        if not isinstance(component_type, str):
            raise InvalidProviderResponseError(
                "Via Lietuva price component has no type"
            )
        component_type = component_type.upper()
        if component_type not in ("ENERGY", "FLAT", "TIME", "PARKING_TIME"):
            unknown_component = True
            continue
        raw_price = component.get("price")
        price = _decimal(raw_price, "component price")
        if price < 0:
            raise InvalidProviderResponseError(
                "Via Lietuva price component cannot be negative"
            )
        vat = _optional_decimal(component.get("vat"), "component VAT")
        if vat is not None:
            if vat < 0:
                raise InvalidProviderResponseError(
                    "Via Lietuva component VAT cannot be negative"
                )
            vat_values.add(vat)
        gross = _gross_price(price, vat, tax_included)
        if gross is None:
            incomplete = True
        if component_type == "PARKING_TIME":
            unsupported_parking_time = True
        elif component_type == "TIME" and gross is not None:
            step_size = component.get("step_size")
            if (
                isinstance(step_size, bool)
                or not isinstance(step_size, int)
                or step_size <= 0
            ):
                raise InvalidProviderResponseError(
                    "Via Lietuva TIME step_size is invalid"
                )
            component_values[component_type].append(gross / Decimal(60))
        else:
            component_values[component_type].append(gross)

    if unknown_component or unsupported_parking_time:
        incomplete = True
    if record.get("min_price") is not None or record.get("max_price") is not None:
        incomplete = True
    energy, energy_ambiguous = _unique_price(component_values["ENERGY"])
    flat, flat_ambiguous = _unique_price(component_values["FLAT"])
    per_minute, time_ambiguous = _unique_price(component_values["TIME"])
    ambiguous = energy_ambiguous or flat_ambiguous or time_ambiguous
    if energy is None:
        incomplete = True

    tariff_status = (
        TariffStatus.AMBIGUOUS
        if ambiguous
        else TariffStatus.INCOMPLETE
        if incomplete
        else TariffStatus.AVAILABLE
    )
    return ChargingTariff(
        currency=currency,
        price_per_kwh=energy,
        session_fee=flat,
        price_per_minute=per_minute,
        description=None,
        source=PROVIDER_VIA_LIETUVA_LT_NAME,
        vat_percentage=next(iter(vat_values)) if len(vat_values) == 1 else None,
        tariff_id=tariff_id,
        tariff_type=tariff_type,
        status=tariff_status,
    )


def _empty_tariff(
    currency: str,
    tariff_id: str,
    tariff_type: str | None,
    status: TariffStatus,
) -> ChargingTariff:
    return ChargingTariff(
        currency=currency,
        price_per_kwh=None,
        session_fee=None,
        price_per_minute=None,
        description=None,
        source=PROVIDER_VIA_LIETUVA_LT_NAME,
        tariff_id=tariff_id,
        tariff_type=tariff_type,
        status=status,
    )


def _tariff_active(record: dict[str, object], now: datetime) -> TariffStatus:
    for field, boundary in (
        ("start_date_time", "start"),
        ("end_date_time", "end"),
    ):
        value = record.get(field)
        if value is None:
            continue
        parsed = _parse_datetime(value, f"tariff {field}")
        if parsed is None:
            return TariffStatus.CONDITIONAL
        if boundary == "start" and parsed > now:
            return TariffStatus.INACTIVE
        if boundary == "end" and parsed < now:
            return TariffStatus.INACTIVE
    return TariffStatus.AVAILABLE


def _element_status(
    restrictions: object, now: datetime, time_zone: str | None
) -> TariffStatus:
    if restrictions is None:
        return TariffStatus.AVAILABLE
    fields = _mapping(restrictions, "tariff restrictions")
    active = True
    for field, before in (("start_date", True), ("end_date", False)):
        value = fields.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            raise InvalidProviderResponseError(
                f"Via Lietuva tariff restriction {field} is invalid"
            )
        if time_zone is None:
            return TariffStatus.CONDITIONAL
        try:
            local_date = now.astimezone(ZoneInfo(time_zone)).date()
            restriction_date = date.fromisoformat(value)
        except (ValueError, ZoneInfoNotFoundError):
            return TariffStatus.CONDITIONAL
        if (before and local_date < restriction_date) or (
            not before and local_date >= restriction_date
        ):
            active = False
    other_conditions = any(
        value is not None
        for key, value in fields.items()
        if key not in ("start_date", "end_date")
    )
    if other_conditions:
        return TariffStatus.CONDITIONAL
    return TariffStatus.AVAILABLE if active else TariffStatus.INACTIVE


def _gross_price(
    price: Decimal, vat: Decimal | None, tax_included: object
) -> Decimal | None:
    if tax_included in ("YES", "N/A"):
        return price
    if tax_included == "NO" and vat is not None:
        return price * (Decimal(1) + vat / Decimal(100))
    return None


def _unique_price(values: list[Decimal | None]) -> tuple[Decimal | None, bool]:
    if not values:
        return None, False
    if any(value is None for value in values):
        return None, False
    unique = set(values)
    if len(unique) != 1:
        return None, True
    return next(iter(unique)), False


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid") from None
    if not result.is_finite():
        raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid")
    return result


def _optional_decimal(value: object, field: str) -> Decimal | None:
    return None if value is None else _decimal(value, field)


def _coordinates(value: object) -> tuple[float, float]:
    coordinates = _mapping(value, "coordinates")
    try:
        latitude = _coordinate_value(coordinates.get("latitude"), "latitude")
        longitude = _coordinate_value(coordinates.get("longitude"), "longitude")
    except InvalidProviderResponseError:
        raise InvalidProviderResponseError(
            "Via Lietuva location coordinates are invalid"
        ) from None
    valid = valid_coordinates(latitude, longitude)
    if valid is None:
        raise InvalidProviderResponseError(
            "Via Lietuva location coordinates are invalid"
        )
    return valid


def _coordinate_value(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid") from None
    if not math.isfinite(result):
        raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid")
    return result


def _aware_datetime(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    return _parse_datetime(value, field)


def _parse_datetime(value: object, field: str) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid") from None
    return parsed if parsed.utcoffset() is not None else None


def _format_address(location: dict[str, object]) -> str | None:
    parts = [
        _optional_string(location.get(key), key)
        for key in ("address", "postal_code", "city")
    ]
    cleaned = [" ".join(part.split()) for part in parts if part]
    return ", ".join(part for part in cleaned if part) or None


def _country_code(value: object, field: str) -> str:
    code = _required_string(value, field).upper()
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid")
    return code


def _identifier(value: object, field: str) -> str:
    if isinstance(value, bool):
        raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid")
    if isinstance(value, int):
        if value <= 0:
            raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid")
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid")


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid")
    return value.strip()


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidProviderResponseError(f"Via Lietuva {field} is invalid")
    return value.strip() or None


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise InvalidProviderResponseError(f"Via Lietuva {field} is not an object")
    return cast(dict[str, object], value)


def _evse_status(value: object) -> EVSEStatus:
    if not isinstance(value, str):
        return EVSEStatus.UNKNOWN
    return _EVSE_STATUS_MAP.get(value.upper(), EVSEStatus.UNKNOWN)
