"""Async client for Eco-Movement's Belgium DATEX II API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
from collections import deque
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TypedDict, cast
from urllib.parse import quote, urljoin, urlsplit

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import (
    COUNTRY_BE,
    DOMAIN,
    MAX_SEARCH_RADIUS_M,
    MIN_SEARCH_RADIUS_M,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_ECO_MOVEMENT_BE_NAME,
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
)
from .base import (
    AuthenticationError,
    BaseChargingProvider,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
    retry_after_seconds,
)

_API_BASE_URL = "https://nap-be.eco-movement.com/datex2/v1"
_LOCATIONS_URL = f"{_API_BASE_URL}/locations"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
_LOCATION_CACHE_SECONDS = 23 * 60 * 60
_LOCATION_SEED_SECONDS = 5 * 60
_IP_REQUEST_INTERVAL = 10
_LOCATION_REQUEST_WINDOW = 60 * 60
_LOCATION_REQUEST_LIMIT = 30
_MAX_LOCATION_PAGES = 100
_EVSE_STATUS_INTERVAL = 30
_REQUEST_LIMITER_DATA_KEY = "belgium_request_limiter"
_LOCATION_CACHE_DATA_KEY = "belgium_location_cache"
_LOGGER = logging.getLogger(__name__)


class _BelgiumEVSENotFound(Exception):
    """The provider does not have status data for this EVSE."""


def is_valid_belgium_api_token(token: object) -> bool:
    """Return whether a token can safely be sent as an HTTP header value."""
    return (
        isinstance(token, str)
        and bool(token)
        and token.isascii()
        and all(33 <= ord(character) <= 126 for character in token)
    )


class BelgiumDatexDocument(TypedDict, total=False):
    """DATEX II v3 publication envelopes returned by the Belgium API."""

    payload: dict[str, object]
    messageContainer: dict[str, object]
    aegiEnergyInfrastructureTablePublication: dict[str, object]
    aegiEnergyInfrastructureStatusPublication: dict[str, object]


def _parse_datex_document(body: bytes) -> BelgiumDatexDocument:
    """Parse and validate a DATEX II response body."""
    try:
        payload: object = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        raise InvalidProviderResponseError(
            "Eco-Movement returned invalid Belgium DATEX II JSON"
        ) from err
    if not isinstance(payload, dict) or any(
        not isinstance(key, str) for key in payload
    ):
        raise InvalidProviderResponseError(
            "Eco-Movement returned an invalid Belgium DATEX II document"
        )
    return cast(BelgiumDatexDocument, payload)


class BelgiumRequestLimiter:
    """Share documented Belgium endpoint and per-IP limits across clients."""

    def __init__(self) -> None:
        """Initialize limiter state for one Home Assistant instance."""
        self._lock = asyncio.Lock()
        self._last_request: float | None = None
        self._retry_after_until = 0.0
        self._location_requests: dict[bytes, deque[float]] = {}
        self._last_evse_status: dict[tuple[bytes, str], float] = {}

    async def async_wait(self, token: str, evse_id: str | None = None) -> None:
        """Wait until this token, EVSE, and IP may safely make another request."""
        token_key = _token_key(token)
        while True:
            async with self._lock:
                now = time.monotonic()
                allowed_at = max(now, self._retry_after_until)
                if self._last_request is not None:
                    allowed_at = max(
                        allowed_at, self._last_request + _IP_REQUEST_INTERVAL
                    )
                if evse_id is None:
                    locations = self._location_requests.setdefault(token_key, deque())
                    while locations and locations[0] <= now - _LOCATION_REQUEST_WINDOW:
                        locations.popleft()
                    if len(locations) >= _LOCATION_REQUEST_LIMIT:
                        allowed_at = max(
                            allowed_at, locations[0] + _LOCATION_REQUEST_WINDOW
                        )
                else:
                    last_status = self._last_evse_status.get((token_key, evse_id))
                    if last_status is not None:
                        allowed_at = max(
                            allowed_at, last_status + _EVSE_STATUS_INTERVAL
                        )

                delay = allowed_at - now
                if delay <= 0:
                    self._last_request = now
                    if evse_id is None:
                        locations.append(now)
                    else:
                        self._last_evse_status[(token_key, evse_id)] = now
                    return
            await asyncio.sleep(delay)

    def defer_requests(self, seconds: int | None) -> None:
        """Pause every client sharing this limiter for the provider's Retry-After."""
        if seconds is not None and seconds > 0:
            self._retry_after_until = max(
                self._retry_after_until, time.monotonic() + seconds
            )


class BelgiumLocationCache:
    """Share validated static snapshots per token across clients.

    A complete snapshot takes roughly 18 paginated location requests, so a
    config flow followed by entry setup would otherwise exhaust the rolling
    location quota and stall setup for up to an hour.
    """

    def __init__(self) -> None:
        """Initialize empty snapshot state."""
        self._snapshots: dict[bytes, tuple[BelgiumDatexDocument, float]] = {}
        self._seeds: dict[
            bytes,
            tuple[BelgiumDatexDocument, str | None, float, asyncio.TimerHandle],
        ] = {}
        self._locks: dict[bytes, asyncio.Lock] = {}

    def lock(self, token: str) -> asyncio.Lock:
        """Return the lock that coalesces snapshot downloads for one token."""
        return self._locks.setdefault(_token_key(token), asyncio.Lock())

    def get(self, token: str) -> tuple[BelgiumDatexDocument, float] | None:
        """Return the cached snapshot and its monotonic timestamp, if any."""
        return self._snapshots.get(_token_key(token))

    def store(self, token: str, document: BelgiumDatexDocument) -> None:
        """Replace the snapshot for one token after it fully validated."""
        self._snapshots[_token_key(token)] = (document, time.monotonic())

    def get_seed(self, token: str) -> tuple[BelgiumDatexDocument, str | None] | None:
        """Return a live first-page seed for this token, if present."""
        key = _token_key(token)
        seed = self._seeds.get(key)
        if seed is None:
            return None
        if seed[2] <= time.monotonic():
            self.discard_seed(token)
            return None
        return seed[0], seed[1]

    def store_seed(
        self,
        token: str,
        document: BelgiumDatexDocument,
        next_url: str | None,
    ) -> None:
        """Keep a validated first page briefly for the background full fetch."""
        key = _token_key(token)
        self.discard_seed(token)
        expires_at = time.monotonic() + _LOCATION_SEED_SECONDS
        handle = asyncio.get_running_loop().call_later(
            _LOCATION_SEED_SECONDS, self._expire_seed, key, expires_at
        )
        self._seeds[key] = (document, next_url, expires_at, handle)

    def discard_seed(
        self, token: str, document: BelgiumDatexDocument | None = None
    ) -> None:
        """Drop a seed, optionally only when it is the supplied document."""
        key = _token_key(token)
        seed = self._seeds.get(key)
        if seed is not None and (document is None or seed[0] is document):
            del self._seeds[key]
            seed[3].cancel()

    def _expire_seed(self, key: bytes, expires_at: float) -> None:
        seed = self._seeds.get(key)
        if seed is not None and seed[2] == expires_at:
            del self._seeds[key]


def _token_key(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def _shared_location_cache(hass: HomeAssistant) -> BelgiumLocationCache:
    """Return the snapshot cache shared by Belgium clients in this HA instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    cache = domain_data.get(_LOCATION_CACHE_DATA_KEY)
    if cache is None:
        cache = BelgiumLocationCache()
        domain_data[_LOCATION_CACHE_DATA_KEY] = cache
    if not isinstance(cache, BelgiumLocationCache):
        raise RuntimeError("CurbCharge Belgium location cache has an invalid type")
    return cache


def _shared_request_limiter(hass: HomeAssistant) -> BelgiumRequestLimiter:
    """Return the one request limiter shared by Belgium clients in this HA instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    limiter = domain_data.get(_REQUEST_LIMITER_DATA_KEY)
    if limiter is None:
        limiter = BelgiumRequestLimiter()
        domain_data[_REQUEST_LIMITER_DATA_KEY] = limiter
    if not isinstance(limiter, BelgiumRequestLimiter):
        raise RuntimeError("CurbCharge Belgium request limiter has an invalid type")
    return limiter


class BelgiumDatexClient:
    """Fetch Belgium locations and per-EVSE DATEX II status documents."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
        request_limiter: BelgiumRequestLimiter,
        location_cache: BelgiumLocationCache | None = None,
    ) -> None:
        """Initialize using Home Assistant's session and shared request limiter."""
        if not is_valid_belgium_api_token(token):
            raise ValueError("The Belgium API token must be printable ASCII.")
        self._session = session
        self._token = token
        self._request_limiter = request_limiter
        self._location_cache = (
            location_cache if location_cache is not None else BelgiumLocationCache()
        )

    @property
    def _locations(self) -> BelgiumDatexDocument | None:
        cached = self._location_cache.get(self._token)
        return cached[0] if cached is not None else None

    @property
    def _locations_cached_at(self) -> float | None:
        cached = self._location_cache.get(self._token)
        return cached[1] if cached is not None else None

    async def async_get_locations(self) -> BelgiumDatexDocument:
        """Return the shared, read-only static snapshot, cached for less than a day."""
        async with self._location_cache.lock(self._token):
            cached = self._location_cache.get(self._token)
            if (
                cached is not None
                and time.monotonic() - cached[1] < _LOCATION_CACHE_SECONDS
            ):
                return cached[0]
            locations = await self._async_fetch_locations()
            self._location_cache.store(self._token, locations)
            self._location_cache.discard_seed(self._token)
            return locations

    async def async_get_locations_for_validation(self) -> BelgiumDatexDocument:
        """Return a fresh snapshot or validate and retain only the first page."""
        async with self._location_cache.lock(self._token):
            cached = self._location_cache.get(self._token)
            if (
                cached is not None
                and time.monotonic() - cached[1] < _LOCATION_CACHE_SECONDS
            ):
                return cached[0]
            seed = self._location_cache.get_seed(self._token)
            if seed is not None:
                return seed[0]

            try:
                document, next_url = await self._async_request_json(_LOCATIONS_URL)
                await asyncio.get_running_loop().run_in_executor(
                    None, _static_tables, document
                )
            except InvalidProviderResponseError as err:
                _LOGGER.warning(
                    "Belgium DATEX II first locations page validation failed: %s",
                    err,
                )
                raise
            self._location_cache.store_seed(self._token, document, next_url)
            return document

    def discard_validation_seed(self, document: BelgiumDatexDocument) -> None:
        """Discard the first-page seed when provider-level validation rejects it."""
        self._location_cache.discard_seed(self._token, document)

    async def _async_fetch_locations(self) -> BelgiumDatexDocument:
        """Download and validate every locations page."""
        tables: list[dict[str, object]] = []
        seen_urls: set[str] = set()
        loop = asyncio.get_running_loop()
        seed = self._location_cache.get_seed(self._token)
        if seed is None:
            url: str | None = _LOCATIONS_URL
            page_number = 0
        else:
            page, url = seed
            page_number = 1
            seen_urls.add(_LOCATIONS_URL)
            tables.extend(await loop.run_in_executor(None, _static_tables, page))
        try:
            while url is not None:
                if page_number >= _MAX_LOCATION_PAGES:
                    raise InvalidProviderResponseError(
                        "Belgium DATEX II exceeded the locations page bound"
                    )
                if url in seen_urls:
                    raise InvalidProviderResponseError(
                        "Belgium DATEX II returned a pagination loop"
                    )
                seen_urls.add(url)
                page_number += 1
                page, url = await self._async_request_json(url)
                tables.extend(await loop.run_in_executor(None, _static_tables, page))
        except (
            AuthenticationError,
            InvalidProviderResponseError,
            ProviderConnectionError,
            ProviderRateLimitError,
        ) as err:
            _LOGGER.warning(
                "Belgium DATEX II locations page %d failed: %s",
                max(page_number, 1),
                err,
            )
            raise
        locations = BelgiumDatexDocument(
            aegiEnergyInfrastructureTablePublication={
                "energyInfrastructureTable": tables
            }
        )
        try:
            _, conflicting = await loop.run_in_executor(
                None, _parse_static_snapshot, locations, 0, 0
            )
        except InvalidProviderResponseError as err:
            _LOGGER.warning(
                "Belgium DATEX II locations snapshot validation failed: %s", err
            )
            raise
        if conflicting:
            _LOGGER.warning(
                "Belgium DATEX II omitted %d EVSEs that the provider published "
                "with conflicting duplicate records",
                conflicting,
            )
        return locations

    async def async_get_evse_status(self, evse_id: str) -> BelgiumDatexDocument | None:
        """Fetch current status and ad-hoc price for one EVSE."""
        if not evse_id:
            raise InvalidProviderResponseError("Belgium returned an empty EVSE id")
        try:
            document, _ = await self._async_request_json(
                f"{_API_BASE_URL}/status/{quote(evse_id, safe='')}",
                evse_id=evse_id,
            )
        except _BelgiumEVSENotFound:
            return None
        return document

    async def _async_request_json(
        self, url: str, *, evse_id: str | None = None
    ) -> tuple[BelgiumDatexDocument, str | None]:
        """Request one documented endpoint without exposing credentials in errors."""
        await self._request_limiter.async_wait(self._token, evse_id)
        try:
            async with self._session.get(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=_REQUEST_TIMEOUT,
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError(
                        "Eco-Movement rejected the Belgium API credentials"
                    )
                if response.status == 404 and evse_id is not None:
                    raise _BelgiumEVSENotFound
                if response.status == 429:
                    retry_after = retry_after_seconds(
                        response.headers.get("Retry-After")
                    )
                    self._request_limiter.defer_requests(retry_after)
                    raise ProviderRateLimitError(
                        "Eco-Movement rate-limited the Belgium API request",
                        retry_after,
                    )
                if not 200 <= response.status < 300:
                    raise ProviderConnectionError(
                        f"Eco-Movement returned Belgium API HTTP {response.status}"
                    )
                body = await response.read()
                next_url = (
                    _next_locations_url(response.headers.get("Link"), url)
                    if evse_id is None
                    else None
                )
        except (
            AuthenticationError,
            ProviderConnectionError,
            ProviderRateLimitError,
        ):
            raise
        except (TimeoutError, aiohttp.ClientError, OSError):
            raise ProviderConnectionError(
                "Could not connect to the Belgium charging-data provider"
            ) from None

        document = await asyncio.get_running_loop().run_in_executor(
            None, _parse_datex_document, body
        )
        return document, next_url


class BelgiumChargingProvider(BaseChargingProvider):
    """Map Belgium DATEX II locations and status into shared station models."""

    def __init__(
        self, client: BelgiumDatexClient, *, static_first: bool = False
    ) -> None:
        """Initialize with a DATEX client using Home Assistant's session.

        With ``static_first``, the first discovery returns static stations
        without per-EVSE status requests, so entities appear as soon as the
        snapshot is available.
        """
        self._client = client
        self._initial_discovery = static_first

    @property
    def provider_name(self) -> str:
        """Return the display name of the provider."""
        return PROVIDER_ECO_MOVEMENT_BE_NAME

    @property
    def provider_id(self) -> str:
        """Return the stable Belgium provider identifier."""
        return PROVIDER_ECO_MOVEMENT_BE

    @property
    def country_code(self) -> str:
        """Return the country served by this provider."""
        return COUNTRY_BE

    @property
    def defers_initial_refresh(self) -> bool:
        """Load in the background: a cold snapshot alone takes about 3 minutes."""
        return True

    async def async_validate_connection(
        self, latitude: float, longitude: float, radius_m: int
    ) -> None:
        """Fetch and validate the static DATEX location document."""
        if not MIN_SEARCH_RADIUS_M <= radius_m <= MAX_SEARCH_RADIUS_M:
            raise UnsupportedLocationError(
                "The Belgium provider does not support this search radius"
            )
        document = await self._client.async_get_locations_for_validation()
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, _parse_static_stations, document, latitude, longitude
            )
        except InvalidProviderResponseError as err:
            self._client.discard_validation_seed(document)
            _LOGGER.warning("Belgium DATEX II locations validation failed: %s", err)
            raise

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
        """Fetch nearby EVSEs and separately selected favourites."""
        document = await self._client.async_get_locations()
        (
            nearby_candidates,
            favourite_stations,
        ) = await asyncio.get_running_loop().run_in_executor(
            None,
            _select_static_stations,
            document,
            latitude,
            longitude,
            radius_m,
            favourites,
            self.provider_id,
        )

        if self._initial_discovery:
            # Publish the static stations as soon as the snapshot is available.
            # Rate-limited per-EVSE status requests follow on the next poll.
            self._initial_discovery = False
            initial_nearby = tuple(nearby_candidates[:max_stations])
            initial_ids = {station.provider_id for station in initial_nearby}
            return StationDiscoveryResult(
                nearby=initial_nearby,
                favourites=tuple(
                    station
                    for station_id, station in favourite_stations.items()
                    if station_id not in initial_ids
                ),
            )

        async def update_status(
            stations: list[ChargingStation],
        ) -> tuple[ChargingStation, ...]:
            updated: list[ChargingStation] = []
            for station in stations:
                if station.evse_id is None:
                    updated.append(station)
                    continue
                document = await self._client.async_get_evse_status(station.evse_id)
                if document is None:
                    updated.append(station)
                    continue
                try:
                    status_record = _evse_status_record(document, station.provider_id)
                except InvalidProviderResponseError as err:
                    _LOGGER.warning(
                        "Belgium DATEX II status validation failed: %s", err
                    )
                    raise
                if status_record is None:
                    updated.append(station)
                    continue
                status = _evse_status(status_record.get("status"))
                if status is EVSEStatus.REMOVED:
                    continue
                status_last_updated = _optional_date(
                    status_record.get("lastUpdated"), "EVSE status"
                )
                raw_energy_rate_updates = status_record.get("energyRateUpdate")
                tariff = (
                    _ad_hoc_tariff(
                        status_record,
                        self.provider_name,
                        station.tariff.currency if station.tariff is not None else None,
                    )
                    if raw_energy_rate_updates
                    else station.tariff
                )
                updated.append(
                    replace(
                        station,
                        availability_reported=True,
                        evse_status=status,
                        last_updated=(
                            status_last_updated
                            if status_last_updated is not None
                            else station.last_updated
                        ),
                        last_updated_source=(
                            ProviderTimestampSource.BELGIUM_EVSE_STATUS
                            if status_last_updated is not None
                            else ProviderTimestampSource.BELGIUM_STATIC_FALLBACK
                        ),
                        tariff=tariff,
                    )
                )
            return tuple(updated)

        checked_ids: set[str] = set()
        favourite_ids = {favourite.provider_id for favourite in favourites}
        if include_unknown:
            selected_candidates = nearby_candidates[:max_stations]
            checked_ids.update(station.provider_id for station in selected_candidates)
            nearby = await update_status(selected_candidates)
        else:
            selected: list[ChargingStation] = []
            for station in nearby_candidates:
                if len(selected) >= max_stations:
                    break
                checked_ids.add(station.provider_id)
                updated = await update_status([station])
                selected.extend(
                    item
                    for item in updated
                    if item.availability_reported or item.provider_id in favourite_ids
                )
            nearby = tuple(selected)

        favourites_to_update = [
            station
            for station_id, station in favourite_stations.items()
            if station_id not in checked_ids
        ]
        return StationDiscoveryResult(
            nearby=nearby,
            favourites=await update_status(favourites_to_update),
        )


def create_belgium_provider(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    token: str,
) -> BelgiumChargingProvider:
    """Create a provider using the limiter shared by this Home Assistant instance."""
    client = BelgiumDatexClient(
        session,
        token,
        _shared_request_limiter(hass),
        _shared_location_cache(hass),
    )
    return BelgiumChargingProvider(client, static_first=True)


def _parse_static_stations(
    document: BelgiumDatexDocument, latitude: float, longitude: float
) -> list[ChargingStation]:
    return _parse_static_snapshot(document, latitude, longitude)[0]


def _parse_static_snapshot(
    document: BelgiumDatexDocument, latitude: float, longitude: float
) -> tuple[list[ChargingStation], int]:
    """Parse static EVSEs and count ids omitted for conflicting duplicates."""
    stations: list[ChargingStation] = []
    for table_value in _static_tables(document):
        table = _datex_object(table_value, "energy-infrastructure table")
        sites = _datex_list(
            table.get("energyInfrastructureSite"), "energy-infrastructure sites"
        )
        for site_value in sites:
            site = _datex_object(site_value, "charging site")
            location = _charging_location(site)
            raw_stations = _datex_list(
                site.get("energyInfrastructureStation"),
                "infrastructure stations",
                optional=True,
            )
            for station_value in raw_stations:
                station = _datex_object(station_value, "infrastructure station")
                station_coordinates = _location_coordinates(
                    station.get("locationReference"), "station coordinates"
                ) or (location.latitude, location.longitude)
                raw_refill_points = _datex_list(
                    station.get("refillPoint"), "refill points", optional=True
                )
                for refill_value in raw_refill_points:
                    refill_point = _datex_object(refill_value, "refill point")
                    evse = _datex_object(
                        refill_point.get("aegiElectricChargingPoint"), "EVSE"
                    )
                    evse_id = _required_datex_string(evse.get("idG"), "EVSE id")
                    connectors, connector_count_reported = _connector_groups(
                        evse_id, evse
                    )
                    external_evse_id = _external_evse_id(evse)
                    stations.append(
                        ChargingStation(
                            provider_id=evse_id,
                            name=external_evse_id or evse_id,
                            latitude=station_coordinates[0],
                            longitude=station_coordinates[1],
                            address=location.address,
                            operator=location.operator,
                            distance_m=distance_m(
                                latitude,
                                longitude,
                                station_coordinates[0],
                                station_coordinates[1],
                            ),
                            connectors=connectors,
                            tariff=_static_ad_hoc_tariff(
                                evse, PROVIDER_ECO_MOVEMENT_BE_NAME
                            ),
                            last_updated=_optional_date(
                                evse.get("lastUpdated"), "EVSE"
                            ),
                            last_updated_source=(
                                ProviderTimestampSource.BELGIUM_STATIC_FALLBACK
                            ),
                            provider=PROVIDER_ECO_MOVEMENT_BE,
                            availability_reported=False,
                            country_code=location.country_code,
                            location=location,
                            provider_name=PROVIDER_ECO_MOVEMENT_BE_NAME,
                            # The status endpoint only resolves the external
                            # EVSE id; the internal idG returns 404 when the
                            # two differ, as they do for most live EVSEs.
                            evse_id=external_evse_id
                            or (evse_id if "entrance" in site else None),
                            connector_count_reported=connector_count_reported,
                        )
                    )
    unique_stations: dict[str, ChargingStation] = {}
    conflicting_ids: set[str] = set()
    for parsed_station in stations:
        previous = unique_stations.get(parsed_station.provider_id)
        if previous is None:
            unique_stations[parsed_station.provider_id] = parsed_station
        elif previous != parsed_station:
            conflicting_ids.add(parsed_station.provider_id)
    # The live feed publishes some EVSE ids under several sites with different
    # coordinates and addresses. Neither copy can be trusted, so omit them
    # instead of rejecting the whole national snapshot.
    for conflicting_id in conflicting_ids:
        del unique_stations[conflicting_id]
    return list(unique_stations.values()), len(conflicting_ids)


def _datex_publications(
    document: BelgiumDatexDocument, name: str
) -> list[dict[str, object]]:
    root = _datex_object(document, "document")
    if name in root:
        return [_datex_object(root[name], "publication")]
    if "messageContainer" in document:
        container = _datex_object(document["messageContainer"], "message container")
        payloads = _datex_list(
            container.get("payload"), "publication payloads", optional=True
        )
    else:
        payloads = [_datex_object(document.get("payload"), "payload")]
    return [
        _datex_object(payload[name], "publication")
        for value in payloads
        if name in (payload := _datex_object(value, "publication payload"))
    ]


def _static_tables(document: BelgiumDatexDocument) -> list[dict[str, object]]:
    publications = _datex_publications(
        document, "aegiEnergyInfrastructureTablePublication"
    )
    if not publications:
        raise InvalidProviderResponseError(
            "Belgium DATEX II returned no infrastructure publication"
        )
    return [
        _datex_object(table, "energy-infrastructure table")
        for publication in publications
        for table in _datex_list(
            publication.get("energyInfrastructureTable"), "energy-infrastructure table"
        )
    ]


def _next_locations_url(link_header: str | None, current_url: str) -> str | None:
    if not link_header:
        return None
    for match in re.finditer(r"<([^>]+)>\s*;([^,]+)", link_header):
        if not re.search(r'\brel="?next"?(?:\s|;|$)', match.group(2), re.IGNORECASE):
            continue
        try:
            candidate = urljoin(current_url, match.group(1))
            parsed = urlsplit(candidate)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname == urlsplit(_LOCATIONS_URL).hostname
                and parsed.port in (None, 443)
                and parsed.path == urlsplit(_LOCATIONS_URL).path
                and parsed.username is None
                and parsed.password is None
                and not parsed.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise InvalidProviderResponseError(
                "Belgium DATEX II returned an invalid pagination link"
            )
        return str(candidate)
    if re.search(r'\brel="?next"?', link_header, re.IGNORECASE):
        raise InvalidProviderResponseError(
            "Belgium DATEX II returned a malformed pagination link"
        )
    return None


def _select_static_stations(
    document: BelgiumDatexDocument,
    latitude: float,
    longitude: float,
    radius_m: int,
    favourites: tuple[FavouriteStationReference, ...],
    provider_id: str,
) -> tuple[list[ChargingStation], dict[str, ChargingStation]]:
    """Parse the snapshot and select nearby and favourited stations."""
    all_stations = _parse_static_stations(document, latitude, longitude)
    stations_by_id = {station.provider_id: station for station in all_stations}
    nearby = [
        station
        for station in all_stations
        if station.distance_m is not None and station.distance_m <= radius_m
    ]
    nearby.sort(
        key=lambda station: (
            station.distance_m is None,
            station.distance_m if station.distance_m is not None else 0,
            station.provider_id,
        )
    )

    favourite_stations: dict[str, ChargingStation] = {}
    for favourite in favourites:
        if favourite.provider != provider_id:
            raise InvalidProviderResponseError(
                "A saved favourite belongs to a different provider"
            )
        station = stations_by_id.get(favourite.provider_id)
        if station is not None:
            favourite_stations[favourite.provider_id] = station
    return nearby, favourite_stations


def _charging_location(site: dict[str, object]) -> ChargingLocation:
    site_id = _required_datex_string(site.get("idG"), "site id")
    coordinates = _location_coordinates(
        _site_location_reference(site), "site coordinates"
    )
    if coordinates is None:
        raise InvalidProviderResponseError(
            "Belgium DATEX II returned a site without coordinates"
        )
    country_code, address = _site_address(site)
    operator = _operator_name(site.get("operator") or site.get("energyDistributor"))
    return ChargingLocation(
        provider_id=site_id,
        provider=PROVIDER_ECO_MOVEMENT_BE,
        provider_name=PROVIDER_ECO_MOVEMENT_BE_NAME,
        country_code=country_code,
        name=address or site_id,
        latitude=coordinates[0],
        longitude=coordinates[1],
        address=address,
        operator=operator,
        last_updated=_optional_date(site.get("lastUpdated"), "charging site"),
    )


def _site_location_reference(site: dict[str, object]) -> dict[str, object]:
    reference = site.get("locationReference")
    if reference is not None:
        return _datex_object(reference, "site location reference")
    for entrance in _datex_list(site.get("entrance"), "site entrances", optional=True):
        reference = _datex_object(entrance, "site entrance")
        if _location_coordinates(reference, "site entrance") is not None:
            return reference
    return {}


def _site_address(site: dict[str, object]) -> tuple[str, str | None]:
    location_reference = _site_location_reference(site)
    area_value = location_reference.get("locAreaLocation")
    if not isinstance(area_value, dict):
        return COUNTRY_BE, None
    area = _datex_object(area_value, "site area")
    extension_value = area.get("locLocationExtensionG")
    if not isinstance(extension_value, dict):
        return COUNTRY_BE, None
    extension = _datex_object(extension_value, "site location extension")
    facility_value = extension.get("FacilityLocation")
    if not isinstance(facility_value, dict):
        return COUNTRY_BE, None
    facility = _datex_object(facility_value, "facility location")
    address_value = facility.get("address")
    if not isinstance(address_value, dict):
        return COUNTRY_BE, None
    address_data = _datex_object(address_value, "site address")

    raw_country = address_data.get("countryCode")
    country_code = (
        _required_datex_string(raw_country, "country code")
        if raw_country is not None
        else COUNTRY_BE
    )
    if country_code != COUNTRY_BE:
        raise InvalidProviderResponseError(
            "Belgium DATEX II returned a location outside Belgium"
        )

    address_lines = _datex_list(
        address_data.get("addressLine"), "address lines", optional=True
    )
    ordered_lines: list[tuple[int, str]] = []
    for index, line_value in enumerate(address_lines):
        line = _datex_object(line_value, "address line")
        text = _localized_text(line.get("text"))
        if text is None:
            continue
        order = line.get("order")
        ordered_lines.append(
            (
                order
                if isinstance(order, int) and not isinstance(order, bool)
                else index,
                text,
            )
        )
    street = " ".join(text for _, text in sorted(ordered_lines))
    postal_code = _optional_datex_string(address_data.get("postcode"), "postal code")
    city = _localized_text(address_data.get("city"))
    locality = " ".join(part for part in (postal_code, city) if part)
    address = ", ".join(part for part in (street, locality) if part)
    return country_code, address or None


def _operator_name(value: object) -> str | None:
    if value is None:
        return None
    operator = _datex_object(value, "operator")
    organisation_value = operator.get("afacAnOrganisation")
    if organisation_value is None:
        return _localized_text(operator.get("name"))
    organisation = _datex_object(organisation_value, "operator organisation")
    return _localized_text(organisation.get("name"))


def _external_evse_id(evse: dict[str, object]) -> str | None:
    identifiers = _identifier_values(evse)
    if identifiers:
        return identifiers[0]
    connector_ids = {
        identifier
        for connector in _datex_list(evse.get("connector"), "connectors", optional=True)
        for identifier in _identifier_values(_datex_object(connector, "connector"))
    }
    if len(connector_ids) > 1:
        raise InvalidProviderResponseError(
            "Belgium DATEX II returned conflicting connector EVSE identifiers"
        )
    return next(iter(connector_ids), None)


def _identifier_values(record: dict[str, object]) -> list[str]:
    identifiers = _datex_list(
        record.get("externalIdentifier"), "EVSE external identifiers", optional=True
    )
    values: list[str] = []
    for identifier_value in identifiers:
        identifier = _datex_object(identifier_value, "EVSE external identifier")
        identifier_type_value = identifier.get("typeOfIdentifier")
        if not isinstance(identifier_type_value, dict):
            continue
        identifier_type = _datex_object(
            identifier_type_value, "EVSE external identifier type"
        )
        if identifier_type.get("extendedValueG") == "evseId":
            value = identifier.get("identifier")
            if isinstance(value, str) and value:
                values.append(value)
    return values


def _connector_groups(
    evse_id: str, evse: dict[str, object]
) -> tuple[tuple[ChargingConnector, ...], bool]:
    raw_connectors = evse.get("connector")
    connectors_reported = raw_connectors is not None
    connector_values = (
        _datex_list(raw_connectors, "connectors") if connectors_reported else []
    )
    declared_count = evse.get("numberOfConnectors")
    if declared_count is None:
        connector_count_reported = connectors_reported
        total = len(connector_values)
    else:
        if (
            isinstance(declared_count, bool)
            or not isinstance(declared_count, int)
            or declared_count < 0
        ):
            raise InvalidProviderResponseError(
                "Belgium DATEX II returned an invalid connector count"
            )
        if declared_count < len(connector_values):
            raise InvalidProviderResponseError(
                "Belgium DATEX II connector list exceeds its reported count"
            )
        connector_count_reported = True
        total = declared_count

    groups: dict[tuple[str | None, float | None], int] = {}
    power_type = _datex_code(evse.get("currentType"))
    for connector_value in connector_values:
        connector = _datex_object(connector_value, "connector")
        connector_type = _datex_code(connector.get("connectorType"))
        raw_power = connector.get("maxPowerAtSocket")
        power_kw = None
        if raw_power is not None:
            power_watts = _datex_number(raw_power, "connector power")
            if power_watts < 0:
                raise InvalidProviderResponseError(
                    "Belgium DATEX II returned negative connector power"
                )
            power_kw = power_watts / 1000
        group = (connector_type, power_kw)
        groups[group] = groups.get(group, 0) + 1

    unknown_count = total - len(connector_values)
    if unknown_count:
        groups[(None, None)] = groups.get((None, None), 0) + unknown_count

    result: list[ChargingConnector] = []
    for (connector_type, power_kw), count in sorted(
        groups.items(),
        key=lambda item: (
            item[0][0] or "",
            item[0][1] if item[0][1] is not None else -1,
        ),
    ):
        signature = json.dumps(
            [connector_type, power_kw],
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        digest = hashlib.sha256(signature).hexdigest()[:12]
        result.append(
            ChargingConnector(
                provider_connector_id=f"{evse_id}:connector:{digest}",
                connector_type=connector_type,
                status=ConnectorStatus.UNKNOWN,
                max_power_kw=power_kw,
                count=count,
                available_count=None,
                power_type=power_type,
                connector_type_id=_belgium_connector_type_id(connector_type),
            )
        )
    return tuple(result), connector_count_reported


def _belgium_connector_type_id(
    connector_type: str | None,
) -> ConnectorTypeId | None:
    if connector_type is None:
        return None
    return {
        "iec62196t2": ConnectorTypeId.TYPE_2,
        "iec62196t2combo": ConnectorTypeId.CCS_COMBO_2,
        "chademo": ConnectorTypeId.CHADEMO,
    }.get(connector_type.casefold())


def _evse_status_record(
    document: BelgiumDatexDocument, evse_id: str
) -> dict[str, object] | None:
    for publication in _datex_publications(
        document, "aegiEnergyInfrastructureStatusPublication"
    ):
        sites = _datex_list(
            publication.get("energyInfrastructureSiteStatus"),
            "site status records",
            optional=True,
        )
        for site_value in sites:
            site = _datex_object(site_value, "site status record")
            stations = _datex_list(
                site.get("energyInfrastructureStationStatus"),
                "station status records",
                optional=True,
            )
            for station_value in stations:
                station = _datex_object(station_value, "station status record")
                refill_points = _datex_list(
                    station.get("refillPointStatus"),
                    "EVSE status records",
                    optional=True,
                )
                for refill_point_value in refill_points:
                    refill_point = _datex_object(
                        refill_point_value, "EVSE status record"
                    )
                    status_value = refill_point.get("aegiElectricChargingPointStatus")
                    if not isinstance(status_value, dict):
                        continue
                    status = _datex_object(status_value, "EVSE status")
                    reference_value = status.get("reference")
                    if not isinstance(reference_value, dict):
                        continue
                    reference = _datex_object(reference_value, "EVSE status reference")
                    if reference.get("idG") == evse_id:
                        return status
    return None


def _evse_status(value: object) -> EVSEStatus:
    status_value = _datex_code(value)
    normalized = status_value.casefold().replace("_", "") if status_value else ""
    return {
        "available": EVSEStatus.AVAILABLE,
        "blocked": EVSEStatus.BLOCKED,
        "charging": EVSEStatus.CHARGING,
        "inoperative": EVSEStatus.INOPERATIVE,
        "outoforder": EVSEStatus.OUT_OF_ORDER,
        "planned": EVSEStatus.PLANNED,
        "removed": EVSEStatus.REMOVED,
        "reserved": EVSEStatus.RESERVED,
    }.get(normalized, EVSEStatus.UNKNOWN)


def _ad_hoc_tariff(
    status: dict[str, object],
    provider_name: str,
    fallback_currency: str | None = None,
) -> ChargingTariff | None:
    """Parse the ad-hoc price in a status record.

    Live status price updates omit ``applicableCurrency`` and only reference the
    static energy rate, so the currency of the static tariff is the fallback.
    """
    raw_updates = status.get("energyRateUpdate")
    if raw_updates is None:
        return None
    return _tariff_from_rates(
        _datex_list(raw_updates, "EVSE energy-rate updates"),
        provider_name,
        require_ad_hoc=False,
        fallback_currency=fallback_currency,
    )


def _static_ad_hoc_tariff(
    evse: dict[str, object], provider_name: str
) -> ChargingTariff | None:
    """Parse the ad-hoc price published with the static EVSE record."""
    rates: list[object] = []
    raw_energy = evse.get("electricEnergy")
    if raw_energy is not None:
        for energy_value in _datex_list(raw_energy, "EVSE energy information"):
            energy = _datex_object(energy_value, "EVSE energy information")
            rates.extend(
                _datex_list(energy.get("energyRate"), "energy rates", optional=True)
            )
    raw_products = evse.get("energyProduct")
    if raw_products is not None:
        for product_value in _datex_list(raw_products, "EVSE energy products"):
            product = _datex_object(product_value, "EVSE energy product")
            energy_value = product.get("aegiElectricEnergy")
            if energy_value is None:
                continue
            energy = _datex_object(energy_value, "EVSE electric energy")
            rates.extend(
                _datex_list(energy.get("energyRate"), "energy rates", optional=True)
            )
    return _tariff_from_rates(rates, provider_name, require_ad_hoc=True)


_PRICE_COMPONENTS = {
    "priceperkwh": "kwh",
    "priceperminute": "minute",
    "flatrate": "flat",
}


def _tariff_from_rates(
    rates: list[object],
    provider_name: str,
    *,
    require_ad_hoc: bool,
    fallback_currency: str | None = None,
) -> ChargingTariff | None:
    """Return a driver-facing tariff when its energy price is unambiguous."""
    prices: dict[str, set[tuple[str, Decimal]]] = {
        component: set() for component in _PRICE_COMPONENTS.values()
    }
    vat_percentages: set[Decimal] = set()
    for rate_value in rates:
        rate = _datex_object(rate_value, "energy rate")
        if (
            require_ad_hoc
            and (_datex_code(rate.get("ratePolicy")) or "").casefold() != "adhoc"
        ):
            continue
        raw_currencies = rate.get("applicableCurrency")
        currency: object
        if raw_currencies is None:
            currency = fallback_currency
        else:
            currencies = _datex_list(raw_currencies, "applicable currencies")
            currency = currencies[0] if len(currencies) == 1 else None
        if (
            not isinstance(currency, str)
            or len(currency) != 3
            or not currency.isalpha()
        ):
            continue
        raw_prices = rate.get("energyPrice")
        if raw_prices is None:
            continue
        for price_value in _datex_list(raw_prices, "energy prices"):
            price = _datex_object(price_value, "energy price")
            component = _PRICE_COMPONENTS.get(
                (_datex_code(price.get("priceType")) or "").casefold()
            )
            # Prices limited to a period or time of day are not the current
            # ad-hoc price, so they are never published as one.
            if (
                component is None
                or price.get("overallPeriod") is not None
                or price.get("timeBasedApplicability") is not None
            ):
                continue
            gross = _gross_price(price)
            if gross is None:
                continue
            amount, vat_percentage = gross
            prices[component].add((currency.upper(), amount))
            if vat_percentage is not None:
                vat_percentages.add(vat_percentage)

    if len({currency for values in prices.values() for currency, _ in values}) != 1:
        return None
    energy_prices = prices["kwh"]
    if len(energy_prices) != 1:
        return None
    currency, price_per_kwh = next(iter(energy_prices))
    return ChargingTariff(
        currency=currency,
        price_per_kwh=price_per_kwh,
        session_fee=_unique_price(prices["flat"]),
        price_per_minute=_unique_price(prices["minute"]),
        description=None,
        source=provider_name,
        vat_percentage=(
            next(iter(vat_percentages)) if len(vat_percentages) == 1 else None
        ),
    )


def _unique_price(values: set[tuple[str, Decimal]]) -> Decimal | None:
    return next(iter(values))[1] if len(values) == 1 else None


def _gross_price(price: dict[str, object]) -> tuple[Decimal, Decimal | None] | None:
    """Return the price including VAT, or None when it cannot be derived.

    The Belgium feed publishes prices excluding VAT with their tax rate.
    """
    amount = _datex_decimal(price.get("value"), "energy price")
    vat_percentage = _vat_percentage(price.get("taxRate"))
    if price.get("taxIncluded") is False:
        if vat_percentage is None:
            return None
        return amount * (Decimal(1) + vat_percentage / Decimal(100)), vat_percentage
    return amount, vat_percentage


def _vat_percentage(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        rate = Decimal(str(value))
    except InvalidOperation:
        return None
    if not rate.is_finite() or not 0 <= rate <= 100:
        return None
    # The live feed mostly reports 21 but occasionally the fraction 0.21.
    return rate.scaleb(2) if 0 < rate < 1 else rate


def _location_coordinates(value: object, field: str) -> tuple[float, float] | None:
    if value is None:
        return None
    reference = _datex_object(value, field)
    for location_type in ("locAreaLocation", "locPointLocation"):
        location_value = reference.get(location_type)
        if not isinstance(location_value, dict):
            continue
        location = _datex_object(location_value, field)
        coordinates_value = location.get("coordinatesForDisplay")
        if not isinstance(coordinates_value, dict):
            continue
        coordinates = _datex_object(coordinates_value, field)
        latitude = _datex_number(coordinates.get("latitude"), "latitude")
        longitude = _datex_number(coordinates.get("longitude"), "longitude")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise InvalidProviderResponseError(
                "Belgium DATEX II returned out-of-range coordinates"
            )
        return latitude, longitude
    return None


def _localized_text(value: object) -> str | None:
    if isinstance(value, str):
        return value or None
    if not isinstance(value, dict):
        return None
    direct_value = value.get("value")
    if isinstance(direct_value, str) and direct_value:
        return direct_value
    values = value.get("values")
    if not isinstance(values, list):
        return None
    for localized_value in values:
        if isinstance(localized_value, dict):
            text = localized_value.get("value")
            if isinstance(text, str) and text:
                return text
    return None


def _datex_code(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        enum_value = value.get("value")
        if isinstance(enum_value, str):
            return enum_value
    return None


def _datex_object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise InvalidProviderResponseError(f"Belgium DATEX II returned invalid {field}")
    return cast(dict[str, object], value)


def _datex_list(value: object, field: str, *, optional: bool = False) -> list[object]:
    if optional and value is None:
        return []
    if not isinstance(value, list):
        raise InvalidProviderResponseError(f"Belgium DATEX II returned invalid {field}")
    return cast(list[object], value)


def _required_datex_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidProviderResponseError(f"Belgium DATEX II returned invalid {field}")
    return value


def _optional_datex_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _required_datex_string(value, field)


def _datex_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidProviderResponseError(f"Belgium DATEX II returned invalid {field}")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidProviderResponseError(f"Belgium DATEX II returned invalid {field}")
    return number


def _datex_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise InvalidProviderResponseError(f"Belgium DATEX II returned invalid {field}")
    try:
        price = Decimal(str(value))
    except InvalidOperation as err:
        raise InvalidProviderResponseError(
            f"Belgium DATEX II returned invalid {field}"
        ) from err
    if not price.is_finite() or price < 0:
        raise InvalidProviderResponseError(f"Belgium DATEX II returned invalid {field}")
    return price


def _optional_date(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidProviderResponseError(
            f"Belgium DATEX II returned invalid {field} timestamp"
        )
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise InvalidProviderResponseError(
            f"Belgium DATEX II returned invalid {field} timestamp"
        ) from err
    if timestamp.tzinfo is None:
        raise InvalidProviderResponseError(
            f"Belgium DATEX II returned a timezone-naive {field} timestamp"
        )
    return timestamp.astimezone(UTC)
