"""Chargy Luxembourg's keyless open-data KML snapshot."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import xml.etree.ElementTree as ET
from dataclasses import replace
from typing import cast

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import (
    COUNTRY_LU,
    DOMAIN,
    MAX_MAX_STATIONS,
    MAX_SEARCH_RADIUS_M,
    MIN_MAX_STATIONS,
    MIN_SEARCH_RADIUS_M,
    PROVIDER_CHARGY_LU,
    PROVIDER_CHARGY_LU_NAME,
)
from ..geography import distance_m
from ..location import valid_coordinates
from ..models import (
    ChargingConnector,
    ChargingStation,
    ConnectorStatus,
    DiscoveryFilterSettings,
    FavouriteStationReference,
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

_RESOURCE_URL = (
    "https://data.public.lu/fr/datasets/r/22f9d77a-5138-4b02-b315-15f306b77034"
)
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)
_SNAPSHOT_DATA_KEY = "luxembourg_snapshot"
_MIN_INTERVAL_SECONDS = 300
_MAX_COOLDOWN_SECONDS = 3600
_KML = "{http://www.opengis.net/kml/2.2}"
_LOGGER = logging.getLogger(__name__)
_STATUS_MAP = {
    "AVAILABLE": ConnectorStatus.AVAILABLE,
    "CHARGING": ConnectorStatus.OCCUPIED,
    "PREPARING": ConnectorStatus.OCCUPIED,
    "FINISHING": ConnectorStatus.OCCUPIED,
    "SUSPENDED_EV": ConnectorStatus.OCCUPIED,
    "SUSPENDED_EVSE": ConnectorStatus.OCCUPIED,
    "FAULT": ConnectorStatus.OUT_OF_SERVICE,
    # Offline/unavailable do not establish physical occupancy or a fault.
    "OFFLINE": ConnectorStatus.UNKNOWN,
    "UNAVAILABLE": ConnectorStatus.UNKNOWN,
}


class LuxembourgSnapshot:
    """Share one neutral snapshot and download/cooldown floor across entries."""

    def __init__(self) -> None:
        """Initialize per-Home Assistant state."""
        self._lock = asyncio.Lock()
        self._stations: tuple[ChargingStation, ...] | None = None
        self._next_allowed_at = 0.0
        self._failure: ChargingProviderError | None = None
        self._cooldown = _MIN_INTERVAL_SECONDS

    async def async_get(
        self, session: aiohttp.ClientSession
    ) -> tuple[ChargingStation, ...]:
        """Refresh at most every five minutes, never hide a failed refresh."""
        async with self._lock:
            now = time.monotonic()
            if now < self._next_allowed_at:
                if self._failure is not None:
                    raise self._failure from None
                if self._stations is not None:
                    return self._stations
                raise ProviderConnectionError("Chargy snapshot refresh is in progress")

            self._next_allowed_at = now + _MIN_INTERVAL_SECONDS
            self._failure = ProviderConnectionError(
                "Chargy snapshot refresh did not complete"
            )
            try:
                stations = await self._async_download(session)
            except ChargingProviderError as err:
                self._failure = err
                raise
            self._stations = stations
            self._failure = None
            self._cooldown = _MIN_INTERVAL_SECONDS
            return stations

    async def _async_download(
        self, session: aiohttp.ClientSession
    ) -> tuple[ChargingStation, ...]:
        """Follow the portal redirect without retaining or exposing its URL."""
        try:
            async with session.get(
                _RESOURCE_URL,
                headers={
                    "Accept": "application/vnd.google-earth.kml+xml, application/xml",
                    "User-Agent": "CurbCharge",
                },
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                if response.status == 429:
                    delay = max(
                        _MIN_INTERVAL_SECONDS,
                        retry_after_seconds(response.headers.get("Retry-After"))
                        or self._cooldown,
                    )
                    self._next_allowed_at = max(
                        self._next_allowed_at, time.monotonic() + delay
                    )
                    self._cooldown = min(self._cooldown * 2, _MAX_COOLDOWN_SECONDS)
                    raise ProviderRateLimitError(
                        "Chargy rate limit reached", retry_after=delay
                    )
                if response.status != 200:
                    raise ProviderConnectionError(
                        f"Chargy snapshot request failed (HTTP {response.status})"
                    )
                body = await response.read()
        except (aiohttp.ClientError, TimeoutError):
            # aiohttp exceptions may contain the keyed redirect URL.
            raise ProviderConnectionError("Unable to fetch Chargy snapshot") from None
        return _parse_snapshot(body)


def create_luxembourg_provider(
    session: aiohttp.ClientSession, hass: HomeAssistant | None = None
) -> LuxembourgChargingProvider:
    """Create a client sharing its snapshot with every Chargy config entry."""
    if hass is None:
        snapshot = LuxembourgSnapshot()
    else:
        domain_data = hass.data.setdefault(DOMAIN, {})
        snapshot = domain_data.setdefault(_SNAPSHOT_DATA_KEY, LuxembourgSnapshot())
        if not isinstance(snapshot, LuxembourgSnapshot):
            raise RuntimeError("CurbCharge Luxembourg snapshot has an invalid type")
    return LuxembourgChargingProvider(session, snapshot)


class LuxembourgChargingProvider(BaseChargingProvider):
    """Normalize Chargy devices without inventing plug types or timestamps."""

    def __init__(
        self, session: aiohttp.ClientSession, snapshot: LuxembourgSnapshot
    ) -> None:
        """Initialize using Home Assistant's shared HTTP session."""
        self._session = session
        self._snapshot = snapshot

    @property
    def provider_name(self) -> str:
        """Return the network-specific display name."""
        return PROVIDER_CHARGY_LU_NAME

    @property
    def provider_id(self) -> str:
        """Return the persisted provider identifier."""
        return PROVIDER_CHARGY_LU

    @property
    def country_code(self) -> str:
        """Return the covered country."""
        return COUNTRY_LU

    async def async_validate_connection(
        self, latitude: float, longitude: float, radius_m: int
    ) -> None:
        """Validate using the same snapshot and request floor as normal polls."""
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
        """Select nearby and favourite devices from the shared country snapshot."""
        _validate_search(latitude, longitude, radius_m, max_stations)
        if any(favourite.provider != self.provider_id for favourite in favourites):
            raise InvalidProviderResponseError("Chargy favourite has another provider")
        snapshot = await self._snapshot.async_get(self._session)
        stations = tuple(
            replace(
                station,
                distance_m=distance_m(
                    latitude, longitude, station.latitude, station.longitude
                ),
            )
            for station in snapshot
        )
        by_id = {station.provider_id: station for station in stations}
        favourite_ids = {favourite.provider_id for favourite in favourites}
        nearby = [
            station
            for station in stations
            if station.distance_m is not None
            and station.distance_m <= radius_m
            and (
                include_unknown
                or station.availability_reported
                or station.provider_id in favourite_ids
            )
        ]
        nearby.sort(key=lambda station: (station.distance_m, station.provider_id))
        return StationDiscoveryResult(
            nearby=tuple(nearby[:max_stations]),
            favourites=tuple(
                by_id[favourite.provider_id]
                for favourite in favourites
                if favourite.provider_id in by_id
            ),
        )


def _validate_search(
    latitude: float, longitude: float, radius_m: int, max_stations: int
) -> None:
    if valid_coordinates(latitude, longitude) is None:
        raise UnsupportedLocationError("Chargy search coordinates are invalid")
    if (
        isinstance(radius_m, bool)
        or not MIN_SEARCH_RADIUS_M <= radius_m <= MAX_SEARCH_RADIUS_M
        or isinstance(max_stations, bool)
        or not MIN_MAX_STATIONS <= max_stations <= MAX_MAX_STATIONS
    ):
        raise UnsupportedLocationError("Chargy search bounds are invalid")


def _parse_snapshot(body: bytes) -> tuple[ChargingStation, ...]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise InvalidProviderResponseError("Chargy returned invalid XML") from None
    if root.tag != f"{_KML}kml":
        raise InvalidProviderResponseError("Chargy response is not KML")
    stations: list[ChargingStation] = []
    device_ids: set[str] = set()
    connector_ids: set[str] = set()
    for placemark in root.findall(f".//{_KML}Placemark"):
        coordinates = placemark.findtext(f"{_KML}Point/{_KML}coordinates", "")
        try:
            parts = coordinates.strip().split(",")
            position = valid_coordinates(float(parts[1]), float(parts[0]))
        except (ValueError, IndexError):
            position = None
        if position is None:
            _LOGGER.warning("Skipping a Chargy location with invalid coordinates")
            continue
        latitude, longitude = position
        name = _text(placemark.findtext(f"{_KML}name"))
        address = placemark.findtext(f"{_KML}address") or None
        records = [
            data
            for data in placemark.findall(f"{_KML}ExtendedData/{_KML}Data")
            if data.get("name") == "chargingdevice"
        ]
        if not records:
            raise InvalidProviderResponseError("Chargy location has no devices")
        for record in records:
            try:
                device = _mapping(json.loads(record.findtext(f"{_KML}value", "")))
            except (ValueError, TypeError):
                raise InvalidProviderResponseError(
                    "Chargy device contains invalid JSON"
                ) from None
            device_id = f"chargy:{_positive_int(device.get('id'))}"
            if device_id in device_ids:
                raise InvalidProviderResponseError("Chargy has duplicate device IDs")
            device_ids.add(device_id)
            device_name = _text(device.get("name"))
            raw_connectors = device.get("connectors")
            if not isinstance(raw_connectors, list) or not raw_connectors:
                raise InvalidProviderResponseError("Chargy device has no connectors")
            if _positive_int(device.get("numberOfConnectors")) != len(raw_connectors):
                raise InvalidProviderResponseError("Chargy connector count differs")
            connectors: list[ChargingConnector] = []
            for raw_connector in raw_connectors:
                connector = _mapping(raw_connector)
                source_id = str(_positive_int(connector.get("id")))
                if source_id in connector_ids:
                    raise InvalidProviderResponseError(
                        "Chargy has duplicate connector IDs"
                    )
                connector_ids.add(source_id)
                socket = _positive_int(connector.get("connector"))
                status = _STATUS_MAP.get(
                    _text(connector.get("description")), ConnectorStatus.UNKNOWN
                )
                power = connector.get("maxchspeed")
                if power is not None and (
                    isinstance(power, bool)
                    or not isinstance(power, (int, float))
                    or not math.isfinite(power)
                    or power <= 0
                ):
                    raise InvalidProviderResponseError("Chargy power is invalid")
                connectors.append(
                    ChargingConnector(
                        provider_connector_id=f"{source_id}:socket:{socket}",
                        connector_type=None,
                        status=status,
                        max_power_kw=float(power) if power is not None else None,
                        available_count=(
                            None
                            if status is ConnectorStatus.UNKNOWN
                            else int(status is ConnectorStatus.AVAILABLE)
                        ),
                        tariff_status=TariffStatus.NOT_REPORTED,
                    )
                )
            stations.append(
                ChargingStation(
                    provider_id=device_id,
                    name=f"{name} - {device_name}" if len(records) > 1 else name,
                    latitude=latitude,
                    longitude=longitude,
                    address=address,
                    operator="Chargy",
                    distance_m=None,
                    connectors=tuple(connectors),
                    tariff=None,
                    last_updated=None,
                    provider=PROVIDER_CHARGY_LU,
                    provider_name=PROVIDER_CHARGY_LU_NAME,
                    country_code=COUNTRY_LU,
                    evse_id=device_name,
                    availability_reported=all(
                        item.status is not ConnectorStatus.UNKNOWN
                        for item in connectors
                    ),
                    tariff_status=TariffStatus.NOT_REPORTED,
                )
            )
    if not stations:
        raise InvalidProviderResponseError("Chargy snapshot has no valid devices")
    return tuple(stations)


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise InvalidProviderResponseError("Chargy record is not an object")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidProviderResponseError("Chargy text field is invalid")
    return value.strip()


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InvalidProviderResponseError("Chargy ID, socket or count is invalid")
    return value
