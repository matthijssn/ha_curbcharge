"""Open Charge Map public, openly licensed station directory."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import OrderedDict
from datetime import UTC, datetime
from urllib.parse import urlsplit

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import (
    DOMAIN,
    MAX_MAX_STATIONS,
    MAX_SEARCH_RADIUS_M,
    MIN_MAX_STATIONS,
    MIN_SEARCH_RADIUS_M,
    PROVIDER_OPEN_CHARGE_MAP,
    PROVIDER_OPEN_CHARGE_MAP_NAME,
)
from ..geography import distance_m
from ..models import (
    ChargingConnector,
    ChargingLocation,
    ChargingStation,
    ConnectorStatus,
    ConnectorTypeId,
    DiscoveryFilterSettings,
    FavouriteStationReference,
    ProviderTimestampSource,
    StationDiscoveryResult,
    TariffStatus,
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

_URL = "https://api.openchargemap.io/v3/poi/"
_TIMEOUT = aiohttp.ClientTimeout(total=20)
_MAX_RESULTS = 500
_MAX_ID_BATCH = 100
_MAX_BODY = 4 * 1024 * 1024
_CACHE_SIZE = 128
_TTL = 3600
_DATA_KEY = "openchargemap_cache"
_PUBLIC_USAGE = frozenset((1, 4, 5, 7))
_CONNECTOR_TYPES = {
    1: ConnectorTypeId.TYPE_1,
    2: ConnectorTypeId.CHADEMO,
    8: ConnectorTypeId.TESLA,
    9: ConnectorTypeId.NEMA_5_20,
    11: ConnectorTypeId.NEMA_14_50,
    22: ConnectorTypeId.NEMA_5_15,
    25: ConnectorTypeId.TYPE_2,
    27: ConnectorTypeId.TESLA,
    30: ConnectorTypeId.TESLA,
    32: ConnectorTypeId.CCS_COMBO_1,
    33: ConnectorTypeId.CCS_COMBO_2,
    1036: ConnectorTypeId.TYPE_2,
}


def is_valid_openchargemap_api_key(value: object) -> bool:
    """Return whether the key can safely be used as an HTTP header value."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and all(character.isprintable() and ord(character) < 127 for character in value)
    )


def create_openchargemap_provider(
    session: aiohttp.ClientSession, api_key: str, hass: HomeAssistant | None = None
) -> OpenChargeMapProvider:
    """Create an adapter sharing the HA instance's cache."""
    if not is_valid_openchargemap_api_key(api_key):
        raise ValueError("The Open Charge Map API key is invalid")
    if hass is None:
        cache = _Cache()
    else:
        cache = hass.data.setdefault(DOMAIN, {}).setdefault(_DATA_KEY, _Cache())
        if not isinstance(cache, _Cache):
            raise RuntimeError("Invalid Open Charge Map cache")
    return OpenChargeMapProvider(session, api_key, cache)


class _Cache:
    def __init__(self) -> None:
        # ponytail: serialize cache misses; per-query locks if throughput matters.
        self.lock = asyncio.Lock()
        self.entries: OrderedDict[
            tuple[bytes, tuple[tuple[str, str], ...]], tuple[float, list[object]]
        ] = OrderedDict()
        self.cooldowns: OrderedDict[bytes, float] = OrderedDict()
        self.inflight: dict[
            tuple[bytes, tuple[tuple[str, str], ...]], asyncio.Task[list[object]]
        ] = {}


class OpenChargeMapProvider(BaseChargingProvider):
    """Read directory records without inferring live availability."""

    def __init__(
        self, session: aiohttp.ClientSession, api_key: str, cache: _Cache
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._key_hash = hashlib.sha256(api_key.encode()).digest()
        self._cache = cache

    @property
    def provider_name(self) -> str:
        return PROVIDER_OPEN_CHARGE_MAP_NAME

    @property
    def provider_id(self) -> str:
        return PROVIDER_OPEN_CHARGE_MAP

    @property
    def country_code(self) -> None:
        return None

    async def async_validate_connection(
        self, latitude: float, longitude: float, radius_m: int
    ) -> None:
        _validate_location(latitude, longitude, radius_m)
        await self._nearby(latitude, longitude, radius_m)

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
        _validate_location(latitude, longitude, radius_m)
        if (
            isinstance(max_stations, bool)
            or not isinstance(max_stations, int)
            or not MIN_MAX_STATIONS <= max_stations <= MAX_MAX_STATIONS
        ):
            raise UnsupportedLocationError("Invalid Open Charge Map station limit")
        if any(f.provider != self.provider_id for f in favourites):
            raise InvalidProviderResponseError("Favorite belongs to another provider")
        ids = tuple(dict.fromkeys(_id(f.provider_id) for f in favourites))
        records = await self._nearby(latitude, longitude, radius_m)
        stations: dict[str, ChargingStation] = {}
        for record in records:
            station = _station(record, latitude, longitude)
            if station is not None and station.distance_m is not None:
                if station.distance_m <= radius_m:
                    stations[station.provider_id] = station
        candidates = [
            station
            for station in stations.values()
            if include_unknown or station.provider_id in ids
        ]
        candidates.sort(key=lambda station: (station.distance_m, station.provider_id))
        nearby = tuple(candidates[:max_stations])
        nearby_ids = {station.provider_id for station in nearby}
        missing = [station_id for station_id in ids if station_id not in nearby_ids]
        found: dict[str, ChargingStation] = {}
        for start in range(0, len(missing), _MAX_ID_BATCH):
            batch = missing[start : start + _MAX_ID_BATCH]
            for record in await self._get({"chargepointid": ",".join(batch)}):
                station = _station(record, latitude, longitude)
                if station is not None and station.provider_id in batch:
                    found[station.provider_id] = station
        return StationDiscoveryResult(
            nearby,
            tuple(found[station_id] for station_id in missing if station_id in found),
            len(records) >= _MAX_RESULTS,
        )

    async def _nearby(
        self, latitude: float, longitude: float, radius_m: int
    ) -> list[object]:
        return await self._get(
            {
                "latitude": str(float(latitude)),
                "longitude": str(float(longitude)),
                "distance": str(radius_m / 1000),
                "distanceunit": "km",
            }
        )

    async def _get(self, params: dict[str, str]) -> list[object]:
        params = {
            "output": "json",
            "maxresults": str(_MAX_RESULTS),
            "opendata": "true",
            "usagetypeid": "1,4,5,7",
            "includecomments": "false",
            "compact": "false",
            "verbose": "true",
            "camelcase": "false",
            **params,
        }
        key = (self._key_hash, tuple(sorted(params.items())))
        task = self._cache.inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch(key, params))
            self._cache.inflight[key] = task

            def cleanup(completed: asyncio.Task[list[object]]) -> None:
                self._cache.inflight.pop(key, None)
                if not completed.cancelled():
                    completed.exception()

            task.add_done_callback(cleanup)
        return await asyncio.shield(task)

    async def _fetch(
        self,
        key: tuple[bytes, tuple[tuple[str, str], ...]],
        params: dict[str, str],
    ) -> list[object]:
        async with self._cache.lock:
            now = time.monotonic()
            cached = self._cache.entries.get(key)
            if cached is not None and now < cached[0]:
                self._cache.entries.move_to_end(key)
                return cached[1]
            if cached is not None:
                del self._cache.entries[key]
            retry_at = self._cache.cooldowns.get(self._key_hash, 0)
            if retry_at > now:
                raise ProviderRateLimitError(
                    "Open Charge Map rate-limited the request",
                    math.ceil(retry_at - now),
                )
            self._cache.cooldowns.pop(self._key_hash, None)
            records = await self._request(params)
            # Validate the entire response before it becomes shared cache state.
            for record in records:
                _station(record, 0, 0)
            self._cache.entries[key] = (time.monotonic() + _TTL, records)
            while len(self._cache.entries) > _CACHE_SIZE:
                self._cache.entries.popitem(last=False)
            return records

    async def _request(self, params: dict[str, str]) -> list[object]:
        try:
            async with self._session.get(
                _URL,
                params=params,
                headers={"X-API-Key": self._api_key, "User-Agent": "CurbCharge/1.0"},
                timeout=_TIMEOUT,
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("Open Charge Map rejected the API key")
                if response.status == 429:
                    delay = retry_after_seconds(response.headers.get("Retry-After"))
                    if delay:
                        self._cache.cooldowns[self._key_hash] = time.monotonic() + delay
                        self._cache.cooldowns.move_to_end(self._key_hash)
                        while len(self._cache.cooldowns) > _CACHE_SIZE:
                            self._cache.cooldowns.popitem(last=False)
                    raise ProviderRateLimitError(
                        "Open Charge Map rate-limited the request", delay
                    )
                if response.status >= 500:
                    raise ProviderConnectionError("Open Charge Map server error")
                if response.status != 200:
                    raise InvalidProviderResponseError(
                        "Open Charge Map rejected the request"
                    )
                body = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    body.extend(chunk)
                    if len(body) > _MAX_BODY:
                        raise InvalidProviderResponseError(
                            "Open Charge Map response too large"
                        )
        except (TimeoutError, aiohttp.ClientError, OSError):
            raise ProviderConnectionError(
                "Could not connect to Open Charge Map"
            ) from None
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise InvalidProviderResponseError(
                "Open Charge Map returned invalid JSON"
            ) from None
        if not isinstance(payload, list) or len(payload) > _MAX_RESULTS:
            raise InvalidProviderResponseError(
                "Open Charge Map returned invalid station collection"
            )
        return payload


def _validate_location(latitude: float, longitude: float, radius: int) -> None:
    for value, bound in ((latitude, 90), (longitude, 180)):
        number = _number(value)
        if number is None or abs(number) > bound:
            raise UnsupportedLocationError("Invalid Open Charge Map coordinates")
    if (
        isinstance(radius, bool)
        or not isinstance(radius, int)
        or not MIN_SEARCH_RADIUS_M <= radius <= MAX_SEARCH_RADIUS_M
    ):
        raise UnsupportedLocationError("Invalid Open Charge Map radius")


def _id(value: object) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 10**20:
        return str(value)
    if (
        isinstance(value, str)
        and len(value) <= 20
        and value.isascii()
        and value.isdecimal()
        and int(value) > 0
    ):
        return str(int(value))
    raise InvalidProviderResponseError("Open Charge Map returned invalid ID")


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise InvalidProviderResponseError("Open Charge Map returned invalid object")
    return value


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _station(
    value: object, latitude: float, longitude: float
) -> ChargingStation | None:
    record = _mapping(value)
    if "UsageTypeID" not in record:
        raise InvalidProviderResponseError(
            "Open Charge Map returned invalid usage type"
        )
    usage = record.get("UsageTypeID")
    source = record.get("DataProvider")
    if usage is None:
        return None
    if isinstance(usage, bool) or not isinstance(usage, int):
        raise InvalidProviderResponseError(
            "Open Charge Map returned invalid usage type"
        )
    if usage not in _PUBLIC_USAGE:
        return None
    if not isinstance(source, dict) or not isinstance(
        source.get("IsOpenDataLicensed"), bool
    ):
        raise InvalidProviderResponseError(
            "Open Charge Map returned invalid data source"
        )
    if not source["IsOpenDataLicensed"]:
        return None
    station_id = _id(record.get("ID"))
    address_info = _mapping(record.get("AddressInfo"))
    lat, lon = (
        _number(address_info.get("Latitude")),
        _number(address_info.get("Longitude")),
    )
    if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180:
        raise InvalidProviderResponseError(
            "Open Charge Map returned invalid coordinates"
        )
    name = _text(address_info.get("Title")) or station_id
    address = (
        ", ".join(
            text
            for field in (
                "AddressLine1",
                "AddressLine2",
                "Town",
                "StateOrProvince",
                "Postcode",
            )
            if (text := _text(address_info.get(field)))
        )
        or None
    )
    country_data = address_info.get("Country")
    country = (
        _text(country_data.get("ISOCode")) if isinstance(country_data, dict) else None
    )
    if country is not None:
        country = (
            country.upper()
            if len(country) == 2 and country.isascii() and country.isalpha()
            else None
        )
    operator_data = record.get("OperatorInfo")
    operator = (
        _text(operator_data.get("Title")) if isinstance(operator_data, dict) else None
    )
    operator_id = record.get("OperatorID")
    operator_id = (
        str(operator_id)
        if isinstance(operator_id, int)
        and not isinstance(operator_id, bool)
        and operator_id > 0
        else None
    )
    updated = _timestamp(record.get("DateLastStatusUpdate"))
    connectors, complete = _connectors(record.get("Connections"), station_id)
    source_url = _text(source.get("WebsiteURL"))
    if source_url:
        try:
            parsed_url = urlsplit(source_url)
            if (
                parsed_url.scheme not in ("http", "https")
                or not parsed_url.hostname
                or parsed_url.username
                or parsed_url.password
            ):
                source_url = None
        except ValueError:
            source_url = None
    location = ChargingLocation(
        station_id,
        PROVIDER_OPEN_CHARGE_MAP,
        PROVIDER_OPEN_CHARGE_MAP_NAME,
        country,
        name,
        lat,
        lon,
        address,
        operator,
        updated,
    )
    return ChargingStation(
        provider_id=station_id,
        name=name,
        latitude=lat,
        longitude=lon,
        address=address,
        operator=operator,
        distance_m=distance_m(latitude, longitude, lat, lon),
        connectors=connectors,
        tariff=None,
        last_updated=updated,
        provider=PROVIDER_OPEN_CHARGE_MAP,
        provider_name=PROVIDER_OPEN_CHARGE_MAP_NAME,
        availability_reported=False,
        is_open=None,
        country_code=country,
        location=location,
        connector_count_reported=complete,
        tariff_status=TariffStatus.NOT_REPORTED,
        last_updated_source=ProviderTimestampSource.OPEN_CHARGE_MAP
        if updated
        else None,
        operator_id=operator_id,
        source_name=_text(source.get("Title")),
        source_url=source_url,
        source_license=_text(source.get("License")),
    )


def _connectors(
    value: object, station_id: str
) -> tuple[tuple[ChargingConnector, ...], bool]:
    if value is None:
        return (), False
    if not isinstance(value, list):
        raise InvalidProviderResponseError(
            "Open Charge Map returned invalid connections"
        )
    groups = []
    complete = bool(value)
    seen: set[str] = set()
    for item in value:
        connection = _mapping(item)
        connection_id = _id(connection.get("ID"))
        if connection_id in seen:
            raise InvalidProviderResponseError(
                "Open Charge Map returned duplicate connection ID"
            )
        seen.add(connection_id)
        quantity = connection.get("Quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
            quantity = 0
            complete = False
        type_id = connection.get("ConnectionTypeID")
        if isinstance(type_id, bool) or not isinstance(type_id, int):
            type_id = None
        detail = connection.get("ConnectionType")
        title = _text(detail.get("Title")) if isinstance(detail, dict) else None
        power = _number(connection.get("PowerKW"))
        if power is not None and power < 0:
            power = None
        current = connection.get("CurrentTypeID")
        power_type = "AC" if current in (10, 20) else "DC" if current == 30 else None
        groups.append(
            ChargingConnector(
                provider_connector_id=f"{station_id}:{connection_id}",
                connector_type=title,
                connector_type_id=_CONNECTOR_TYPES.get(type_id)
                if type_id is not None
                else None,
                status=ConnectorStatus.UNKNOWN,
                max_power_kw=power,
                count=quantity,
                available_count=None,
                power_type=power_type,
            )
        )
    return tuple(groups), complete
