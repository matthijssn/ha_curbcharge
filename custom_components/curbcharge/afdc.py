"""AFDC/NLR provider for public U.S. EV charging stations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from datetime import UTC, datetime

import aiohttp
from homeassistant.core import HomeAssistant

from .const import (
    COUNTRY_US,
    DOMAIN,
    MAX_MAX_STATIONS,
    MAX_SEARCH_RADIUS_M,
    MIN_MAX_STATIONS,
    MIN_SEARCH_RADIUS_M,
    PROVIDER_AFDC_US,
    PROVIDER_AFDC_US_NAME,
)
from .geography import distance_m
from .models import (
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
from .provider import (
    AuthenticationError,
    BaseChargingProvider,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
    retry_after_seconds,
)

_NEAREST_API_URL = "https://developer.nlr.gov/api/alt-fuel-stations/v1/nearest.json"
_ALL_API_URL = "https://developer.nlr.gov/api/alt-fuel-stations/v1.json"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
_MAX_NEAREST_RESULTS = 200
_MAX_ID_BATCH = 200
_METERS_PER_MILE = 1609.344
_REQUEST_INTERVAL_SECONDS = 3.61
_REQUEST_LIMITER_DATA_KEY = "afdc_request_limiter"

_CONNECTOR_TYPES: dict[str, tuple[str, ConnectorTypeId]] = {
    "J1772": ("J1772 (Type 1)", ConnectorTypeId.TYPE_1),
    "J1772COMBO": ("CCS Combo 1", ConnectorTypeId.CCS_COMBO_1),
    "TESLA": ("Tesla", ConnectorTypeId.TESLA),
    "CHADEMO": ("CHAdeMO", ConnectorTypeId.CHADEMO),
    "NEMA515": ("NEMA 5-15", ConnectorTypeId.NEMA_5_15),
    "NEMA520": ("NEMA 5-20", ConnectorTypeId.NEMA_5_20),
    "NEMA1450": ("NEMA 14-50", ConnectorTypeId.NEMA_14_50),
    "J3271": ("SAE J3271", ConnectorTypeId.SAE_J3271),
}
_FALLBACK_LEVELS = (
    ("ev_level1_evse_num", "level1", "AC"),
    ("ev_level2_evse_num", "level2", "AC"),
    ("ev_dc_fast_num", "dc_fast", "DC"),
)


def is_valid_afdc_api_key(value: object) -> bool:
    """Return whether an NLR API key is a non-empty printable string."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and all(character.isprintable() for character in value)
    )


def create_afdc_provider(
    session: aiohttp.ClientSession,
    api_key: str,
    hass: HomeAssistant | None = None,
) -> AFDCProvider:
    """Create the U.S. AFDC provider with a shared per-key rate limiter."""
    if not is_valid_afdc_api_key(api_key):
        raise ValueError("The AFDC API key must be a non-empty printable string")
    limiter = (
        _shared_request_limiter(hass) if hass is not None else AFDCRequestLimiter()
    )
    return AFDCProvider(session, api_key, limiter)


class AFDCRequestLimiter:
    """Share NLR's hourly request quota and server cooldown by API key."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_request: dict[bytes, float] = {}
        self._retry_after_until: dict[bytes, float] = {}

    async def async_wait(self, api_key: str) -> None:
        """Keep requests below NLR's 1,000-per-hour per-key quota."""
        key = hashlib.sha256(api_key.encode()).digest()
        while True:
            async with self._lock:
                now = time.monotonic()
                allowed_at = self._retry_after_until.get(key, 0.0)
                last_request = self._last_request.get(key)
                if last_request is not None:
                    allowed_at = max(
                        allowed_at, last_request + _REQUEST_INTERVAL_SECONDS
                    )
                delay = allowed_at - now
                if delay <= 0:
                    self._last_request[key] = now
                    return
            await asyncio.sleep(delay)

    def defer_for_retry_after(self, api_key: str, seconds: int | None) -> None:
        """Share a server-provided cooldown with other entries using this key."""
        if seconds is None or seconds <= 0:
            return
        key = hashlib.sha256(api_key.encode()).digest()
        self._retry_after_until[key] = max(
            self._retry_after_until.get(key, 0.0),
            time.monotonic() + seconds,
        )


def _shared_request_limiter(hass: HomeAssistant) -> AFDCRequestLimiter:
    """Return the NLR limiter shared by AFDC entries in this HA instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    limiter = domain_data.get(_REQUEST_LIMITER_DATA_KEY)
    if limiter is None:
        limiter = AFDCRequestLimiter()
        domain_data[_REQUEST_LIMITER_DATA_KEY] = limiter
    if not isinstance(limiter, AFDCRequestLimiter):
        raise RuntimeError("CurbCharge AFDC request limiter has an invalid type")
    return limiter


class AFDCProvider(BaseChargingProvider):
    """Query public U.S. station records and preserve unknown live availability."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        request_limiter: AFDCRequestLimiter,
    ) -> None:
        self._session = session
        self._api_key = api_key
        self._request_limiter = request_limiter

    @property
    def provider_name(self) -> str:
        return PROVIDER_AFDC_US_NAME

    @property
    def provider_id(self) -> str:
        return PROVIDER_AFDC_US

    @property
    def country_code(self) -> str:
        return COUNTRY_US

    async def async_validate_connection(
        self, latitude: float, longitude: float, radius_m: int
    ) -> None:
        """Validate API credentials and the nearby-stations response shape."""
        _validate_search_location(latitude, longitude, radius_m)
        records = await self._async_nearby(latitude, longitude, radius_m, limit=1)
        for record in records:
            _station_from_record(record, latitude, longitude)

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
        """Return nearby stations and refresh saved favorites by AFDC ID."""
        _validate_search(latitude, longitude, radius_m, max_stations)
        if any(favourite.provider != self.provider_id for favourite in favourites):
            raise InvalidProviderResponseError(
                "A saved favorite belongs to a different provider"
            )

        favourite_ids = tuple(
            dict.fromkeys(_station_id(f.provider_id) for f in favourites)
        )
        nearby_records = await self._async_nearby(
            latitude, longitude, radius_m, limit=_MAX_NEAREST_RESULTS
        )
        stations_by_id: dict[str, ChargingStation] = {}
        for record in nearby_records:
            station = _station_from_record(record, latitude, longitude)
            if station is not None and station.distance_m is not None:
                if station.distance_m <= radius_m:
                    stations_by_id[station.provider_id] = station

        favourite_id_set = set(favourite_ids)
        nearby_candidates = [
            station
            for station in stations_by_id.values()
            if include_unknown
            or station.availability_reported
            or station.provider_id in favourite_id_set
        ]
        nearby_candidates.sort(
            key=lambda station: (station.distance_m, station.provider_id)
        )
        nearby = tuple(nearby_candidates[:max_stations])
        nearby_ids = {station.provider_id for station in nearby}

        missing_favourite_ids = [
            station_id for station_id in favourite_ids if station_id not in nearby_ids
        ]
        favourite_stations_by_id: dict[str, ChargingStation] = {}
        for start in range(0, len(missing_favourite_ids), _MAX_ID_BATCH):
            station_ids = missing_favourite_ids[start : start + _MAX_ID_BATCH]
            records = await self._async_get_by_ids(station_ids)
            for record in records:
                station = _station_from_record(record, latitude, longitude)
                if station is not None and station.provider_id in station_ids:
                    favourite_stations_by_id[station.provider_id] = station

        favourite_stations = tuple(
            favourite_stations_by_id[station_id]
            for station_id in favourite_ids
            if station_id not in nearby_ids and station_id in favourite_stations_by_id
        )
        return StationDiscoveryResult(nearby=nearby, favourites=favourite_stations)

    async def _async_nearby(
        self,
        latitude: float,
        longitude: float,
        radius_m: int,
        *,
        limit: int,
    ) -> list[object]:
        _validate_search_location(latitude, longitude, radius_m)
        payload = await self._async_get_json(
            _NEAREST_API_URL,
            {
                "country": COUNTRY_US,
                "fuel_type": "ELEC",
                "access": "public",
                "latitude": f"{latitude:.6f}",
                "longitude": f"{longitude:.6f}",
                "radius": f"{radius_m / _METERS_PER_MILE:.6f}",
                "limit": str(min(limit, _MAX_NEAREST_RESULTS)),
            },
        )
        return _station_records(payload)

    async def _async_get_by_ids(self, station_ids: list[str]) -> list[object]:
        payload = await self._async_get_json(
            _ALL_API_URL,
            {
                "country": COUNTRY_US,
                "fuel_type": "ELEC",
                "access": "public",
                "ids": ",".join(station_ids),
                "limit": "all",
            },
        )
        return _station_records(payload)

    async def _async_get_json(self, url: str, parameters: dict[str, str]) -> object:
        await self._request_limiter.async_wait(self._api_key)
        try:
            async with self._session.get(
                url,
                params={**parameters, "api_key": self._api_key},
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("NLR rejected the API key")
                if response.status == 429:
                    retry_after = retry_after_seconds(
                        response.headers.get("Retry-After")
                    )
                    self._request_limiter.defer_for_retry_after(
                        self._api_key, retry_after
                    )
                    raise ProviderRateLimitError(
                        "NLR rate-limited the request", retry_after
                    )
                if 500 <= response.status < 600:
                    raise ProviderConnectionError(
                        "NLR returned a temporary server error"
                    )
                if response.status < 200 or response.status >= 300:
                    raise InvalidProviderResponseError(
                        "NLR rejected the station request"
                    )
                body = await response.read()
        except (
            AuthenticationError,
            InvalidProviderResponseError,
            ProviderConnectionError,
            ProviderRateLimitError,
        ):
            raise
        except (TimeoutError, aiohttp.ClientError, OSError):
            raise ProviderConnectionError("Could not connect to NLR") from None

        try:
            return json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise InvalidProviderResponseError("NLR returned invalid JSON") from err


def _validate_search_location(latitude: float, longitude: float, radius_m: int) -> None:
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
        raise UnsupportedLocationError("The AFDC search location is invalid")
    if not MIN_SEARCH_RADIUS_M <= radius_m <= MAX_SEARCH_RADIUS_M:
        raise UnsupportedLocationError("The AFDC search radius is unsupported")


def _validate_search(
    latitude: float, longitude: float, radius_m: int, max_stations: int
) -> None:
    _validate_search_location(latitude, longitude, radius_m)
    if not MIN_MAX_STATIONS <= max_stations <= MAX_MAX_STATIONS:
        raise UnsupportedLocationError("The AFDC station limit is unsupported")


def _station_records(payload: object) -> list[object]:
    response = _mapping(payload, "station response")
    records = response.get("fuel_stations")
    if not isinstance(records, list):
        raise InvalidProviderResponseError("NLR returned an invalid station collection")
    return records


def _station_from_record(
    value: object, latitude: float, longitude: float
) -> ChargingStation | None:
    record = _mapping(value, "station")
    fuel_type = _optional_string(record.get("fuel_type_code"), "fuel type")
    country = _optional_string(record.get("country"), "country")
    access = _optional_string(record.get("access_code"), "access code")
    if fuel_type != "ELEC" or country != COUNTRY_US or access is None:
        return None
    if access.casefold() != "public":
        return None

    station_id = _station_id(record.get("id"))
    station_latitude = _required_number(record.get("latitude"), "latitude")
    station_longitude = _required_number(record.get("longitude"), "longitude")
    if not -90 <= station_latitude <= 90 or not -180 <= station_longitude <= 180:
        raise InvalidProviderResponseError("NLR returned invalid station coordinates")

    street = _optional_string(record.get("street_address"), "street address")
    city = _optional_string(record.get("city"), "city")
    state = _optional_string(record.get("state"), "state")
    postal_code = _optional_string(record.get("zip"), "postal code")
    locality = " ".join(value for value in (city, state, postal_code) if value)
    address = ", ".join(value for value in (street, locality) if value) or None
    display_name = _optional_string(record.get("station_name"), "station name")
    display_name = display_name or address or station_id
    operator = _optional_string(record.get("ev_network"), "EV network")
    last_updated = _optional_timestamp(record.get("updated_at"))
    connectors, connector_count_reported = _connector_groups(station_id, record)

    location = ChargingLocation(
        provider_id=station_id,
        provider=PROVIDER_AFDC_US,
        provider_name=PROVIDER_AFDC_US_NAME,
        country_code=COUNTRY_US,
        name=display_name,
        latitude=station_latitude,
        longitude=station_longitude,
        address=address,
        operator=operator,
        last_updated=last_updated,
    )
    return ChargingStation(
        provider_id=station_id,
        name=display_name,
        latitude=station_latitude,
        longitude=station_longitude,
        address=address,
        operator=operator,
        distance_m=distance_m(latitude, longitude, station_latitude, station_longitude),
        connectors=connectors,
        tariff=None,
        last_updated=last_updated,
        provider=PROVIDER_AFDC_US,
        availability_reported=False,
        is_open=_station_is_open(record.get("status_code")),
        country_code=COUNTRY_US,
        location=location,
        provider_name=PROVIDER_AFDC_US_NAME,
        connector_count_reported=connector_count_reported,
        tariff_status=TariffStatus.NOT_REPORTED,
        last_updated_source=(
            ProviderTimestampSource.AFDC if last_updated is not None else None
        ),
    )


def _connector_groups(
    station_id: str, record: dict[str, object]
) -> tuple[tuple[ChargingConnector, ...], bool]:
    raw_units = record.get("ev_charging_units")
    if raw_units is not None and not isinstance(raw_units, list):
        raise InvalidProviderResponseError("NLR returned invalid EV charging units")

    if isinstance(raw_units, list) and raw_units:
        connectors: list[ChargingConnector] = []
        all_counts_reported = True
        any_count_reported = False
        for unit_index, raw_unit in enumerate(raw_units):
            unit = _mapping(raw_unit, "EV charging unit")
            level = _optional_string(unit.get("charging_level"), "charging level")
            power_type = _power_type(level)
            raw_connectors = unit.get("connectors")
            if raw_connectors is None:
                all_counts_reported = False
                continue
            connector_data = _mapping(raw_connectors, "EV connectors")
            for connector_code, raw_connector in connector_data.items():
                connector = _mapping(raw_connector, "EV connector")
                count = _optional_nonnegative_int(
                    connector.get("port_count"), "connector port count"
                )
                if count is None:
                    all_counts_reported = False
                    continue
                any_count_reported = True
                if count == 0:
                    continue
                power = _optional_number(connector.get("power_kw"), "connector power")
                display_type, type_id = _connector_type(connector_code)
                connectors.append(
                    ChargingConnector(
                        provider_connector_id=(
                            f"{station_id}:unit:{unit_index}:{connector_code}"
                        ),
                        connector_type=display_type,
                        connector_type_id=type_id,
                        status=ConnectorStatus.UNKNOWN,
                        max_power_kw=power,
                        count=count,
                        available_count=None,
                        power_type=power_type,
                    )
                )
        if any_count_reported:
            return tuple(connectors), all_counts_reported

    return _aggregate_connector_groups(station_id, record)


def _aggregate_connector_groups(
    station_id: str, record: dict[str, object]
) -> tuple[tuple[ChargingConnector, ...], bool]:
    connectors: list[ChargingConnector] = []
    complete = True
    any_count_reported = False
    for field, level, power_type in _FALLBACK_LEVELS:
        count = _optional_nonnegative_int(record.get(field), field)
        if count is None:
            complete = False
            continue
        any_count_reported = True
        if count == 0:
            continue
        connectors.append(
            ChargingConnector(
                provider_connector_id=f"{station_id}:{level}",
                connector_type=None,
                connector_type_id=None,
                status=ConnectorStatus.UNKNOWN,
                max_power_kw=None,
                count=count,
                available_count=None,
                power_type=power_type,
            )
        )
    return tuple(connectors), any_count_reported and complete


def _connector_type(code: str) -> tuple[str, ConnectorTypeId | None]:
    connector = _CONNECTOR_TYPES.get(code.upper())
    if connector is not None:
        return connector
    return code, None


def _power_type(level: str | None) -> str | None:
    if level is None:
        return None
    normalized = level.casefold()
    if normalized == "dc_fast":
        return "DC"
    if normalized in ("1", "2"):
        return "AC"
    return None


def _station_is_open(value: object) -> bool | None:
    status = _optional_string(value, "station status")
    return {"E": True, "P": False, "T": False}.get(status.upper()) if status else None


def _station_id(value: object) -> str:
    if isinstance(value, bool):
        raise InvalidProviderResponseError("NLR returned invalid station ID")
    if isinstance(value, int) and value >= 0:
        return str(value)
    if isinstance(value, str) and value.isdigit():
        return value
    raise InvalidProviderResponseError("NLR returned invalid station ID")


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise InvalidProviderResponseError(f"NLR returned invalid {field}")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidProviderResponseError(f"NLR returned invalid {field}")
    return value.strip() or None


def _required_number(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise InvalidProviderResponseError(f"NLR returned invalid {field}")
    return float(value)


def _optional_number(value: object, field: str) -> float | None:
    if value is None:
        return None
    number = _required_number(value, field)
    if number < 0:
        raise InvalidProviderResponseError(f"NLR returned invalid {field}")
    return number


def _optional_nonnegative_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidProviderResponseError(f"NLR returned invalid {field}")
    return value


def _optional_timestamp(value: object) -> datetime | None:
    raw = _optional_string(value, "updated_at")
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as err:
        raise InvalidProviderResponseError("NLR returned invalid timestamp") from err
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
