"""NOBIL v3 client and Norway/Sweden provider mapping."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import aiohttp
from homeassistant.core import HomeAssistant

from .const import (
    COUNTRY_NO,
    COUNTRY_SE,
    DOMAIN,
    MAX_MAX_STATIONS,
    MAX_SEARCH_RADIUS_M,
    MIN_MAX_STATIONS,
    MIN_SEARCH_RADIUS_M,
    PROVIDER_NOBIL_NO,
    PROVIDER_NOBIL_NO_NAME,
    PROVIDER_NOBIL_SE,
    PROVIDER_NOBIL_SE_NAME,
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
    UnsupportedProviderError,
    retry_after_seconds,
)

_API_URL = "https://nobil.no/api/server/search.php"
_API_VERSION = "3"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
_MAX_RESPONSE_RECORDS = 2000
_MAX_TRANSIENT_RETRIES = 2
_TRANSIENT_RETRY_BACKOFF = (1, 2)
_TRANSIENT_BACKOFF_BASE_SECONDS = 30
_TRANSIENT_BACKOFF_MAX_SECONDS = 300
_REQUEST_LIMITER_DATA_KEY = "nobil_request_limiter"
_POSITION_PATTERN = re.compile(
    r"^\s*\(\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*,"
    r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*\)\s*$"
)


@dataclass(frozen=True, slots=True)
class _NobilCountry:
    provider_id: str
    provider_name: str
    country_code: str
    api_country_code: str


_COUNTRIES = {
    PROVIDER_NOBIL_NO: _NobilCountry(
        provider_id=PROVIDER_NOBIL_NO,
        provider_name=PROVIDER_NOBIL_NO_NAME,
        country_code=COUNTRY_NO,
        api_country_code="NOR",
    ),
    PROVIDER_NOBIL_SE: _NobilCountry(
        provider_id=PROVIDER_NOBIL_SE,
        provider_name=PROVIDER_NOBIL_SE_NAME,
        country_code=COUNTRY_SE,
        api_country_code="SWE",
    ),
}


def is_valid_nobil_api_key(value: object) -> bool:
    """Return whether a NOBIL API key is a non-empty printable string."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and all(character.isprintable() for character in value)
    )


def create_nobil_provider(
    session: aiohttp.ClientSession,
    provider_id: str,
    api_key: str,
    hass: HomeAssistant | None = None,
) -> NobilProvider:
    """Create the country-specific NOBIL provider choice."""
    country = _COUNTRIES.get(provider_id)
    if country is None:
        raise UnsupportedProviderError("The requested NOBIL country is unsupported")
    if not is_valid_nobil_api_key(api_key):
        raise ValueError("The NOBIL API key must be a non-empty printable string")
    request_limiter = (
        _shared_request_limiter(hass) if hass is not None else NobilRequestLimiter()
    )
    return NobilProvider(session, country, api_key, request_limiter)


class NobilRequestLimiter:
    """Share server-directed cooldowns and bounded transient backoff by API key."""

    def __init__(self) -> None:
        """Initialize request cooldown state."""
        self._lock = asyncio.Lock()
        self._retry_after_until: dict[bytes, float] = {}
        self._transient_failures: dict[bytes, int] = {}

    async def async_wait(self, api_key: str) -> None:
        """Wait for a cooldown set by a prior response for the same key."""
        key = hashlib.sha256(api_key.encode()).digest()
        while True:
            async with self._lock:
                retry_until = self._retry_after_until.get(key, 0.0)
            delay = retry_until - time.monotonic()
            if delay <= 0:
                return
            await asyncio.sleep(delay)

    def defer_for_retry_after(self, api_key: str, seconds: int | None) -> None:
        """Share the server's Retry-After delay with other clients using this key."""
        if seconds is not None and seconds > 0:
            self._defer(api_key, seconds)

    def defer_for_transient_failure(self, api_key: str) -> None:
        """Apply capped exponential backoff after retryable transient failures."""
        key = hashlib.sha256(api_key.encode()).digest()
        failures = min(self._transient_failures.get(key, 0) + 1, 16)
        self._transient_failures[key] = failures
        delay = min(
            _TRANSIENT_BACKOFF_BASE_SECONDS * 2 ** (failures - 1),
            _TRANSIENT_BACKOFF_MAX_SECONDS,
        )
        self._defer(api_key, delay)

    def reset_transient_failures(self, api_key: str) -> None:
        """Reset the transient backoff after a successful API response."""
        key = hashlib.sha256(api_key.encode()).digest()
        self._transient_failures.pop(key, None)

    def _defer(self, api_key: str, seconds: int) -> None:
        key = hashlib.sha256(api_key.encode()).digest()
        self._retry_after_until[key] = max(
            self._retry_after_until.get(key, 0.0),
            time.monotonic() + seconds,
        )


def _shared_request_limiter(hass: HomeAssistant) -> NobilRequestLimiter:
    """Return the request cooldown shared by NOBIL entries in this HA instance."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    limiter = domain_data.get(_REQUEST_LIMITER_DATA_KEY)
    if limiter is None:
        limiter = NobilRequestLimiter()
        domain_data[_REQUEST_LIMITER_DATA_KEY] = limiter
    if not isinstance(limiter, NobilRequestLimiter):
        raise RuntimeError("CurbCharge NOBIL request limiter has an invalid type")
    return limiter


class NobilProvider(BaseChargingProvider):
    """Fetch NOBIL stations for one country through the shared v3 API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        country: _NobilCountry,
        api_key: str,
        request_limiter: NobilRequestLimiter,
    ) -> None:
        """Initialize the NOBIL client using Home Assistant's shared session."""
        self._session = session
        self._country = country
        self._api_key = api_key
        self._request_limiter = request_limiter

    @property
    def provider_name(self) -> str:
        """Return the country-specific NOBIL provider name."""
        return self._country.provider_name

    @property
    def provider_id(self) -> str:
        """Return the stable provider identifier for this country choice."""
        return self._country.provider_id

    @property
    def country_code(self) -> str:
        """Return the ISO 3166-1 alpha-2 country code."""
        return self._country.country_code

    async def async_validate_connection(
        self, latitude: float, longitude: float, radius_m: int
    ) -> None:
        """Verify credentials and validate the NOBIL response shape."""
        records = await self._async_search(latitude, longitude, radius_m, limit=1)
        for record in records:
            _station_from_record(record, self._country, latitude, longitude)

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
        """Return nearby stations and refresh saved favourites when possible."""
        _validate_search(latitude, longitude, radius_m, max_stations)
        if any(favourite.provider != self.provider_id for favourite in favourites):
            raise InvalidProviderResponseError(
                "A saved favourite belongs to a different NOBIL country provider"
            )

        records = await self._async_search(
            latitude, longitude, radius_m, limit=MAX_MAX_STATIONS
        )
        stations_by_id: dict[str, ChargingStation] = {}
        for record in records:
            station = _station_from_record(record, self._country, latitude, longitude)
            if station is not None:
                stations_by_id[station.provider_id] = station

        favourite_ids = {favourite.provider_id for favourite in favourites}
        nearby_candidates = [
            station
            for station in stations_by_id.values()
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
        nearby_ids = {station.provider_id for station in nearby}

        favourite_stations: dict[str, ChargingStation] = {}
        for favourite in favourites:
            if favourite.provider_id in nearby_ids:
                continue

            station = stations_by_id.get(favourite.provider_id)
            if station is None:
                favourite_records = await self._async_search(
                    favourite.latitude,
                    favourite.longitude,
                    MIN_SEARCH_RADIUS_M,
                    limit=MAX_MAX_STATIONS,
                )
                station = next(
                    (
                        parsed
                        for record in favourite_records
                        if (
                            parsed := _station_from_record(
                                record, self._country, latitude, longitude
                            )
                        )
                        is not None
                        and parsed.provider_id == favourite.provider_id
                    ),
                    None,
                )
            if station is not None:
                favourite_stations[favourite.provider_id] = station

        return StationDiscoveryResult(
            nearby=nearby,
            favourites=tuple(favourite_stations.values()),
        )

    async def _async_search(
        self,
        latitude: float,
        longitude: float,
        radius_m: int,
        *,
        limit: int,
    ) -> list[object]:
        """Query NOBIL's documented nearest-stations search."""
        _validate_search(latitude, longitude, radius_m, limit)
        payload = await self._async_request(
            {
                "apikey": self._api_key,
                "apiversion": _API_VERSION,
                "action": "search",
                "type": "near",
                "lat": f"{latitude:.6f}",
                "long": f"{longitude:.6f}",
                "distance": str(radius_m),
                "limit": str(min(limit, _MAX_RESPONSE_RECORDS)),
                "format": "json",
            }
        )
        records = _station_records(payload)
        if len(records) > _MAX_RESPONSE_RECORDS:
            raise InvalidProviderResponseError(
                "NOBIL returned more than the documented response limit"
            )
        return records

    async def _async_request(self, parameters: dict[str, str]) -> object:
        """Send requests with bounded transient retries and rate-limit handling."""
        attempt = 0
        while True:
            await self._request_limiter.async_wait(self._api_key)
            try:
                body = await self._async_request_body(parameters)
            except ProviderConnectionError:
                if attempt == _MAX_TRANSIENT_RETRIES:
                    self._request_limiter.defer_for_transient_failure(self._api_key)
                    raise
                await asyncio.sleep(_TRANSIENT_RETRY_BACKOFF[attempt])
                attempt += 1
                continue

            self._request_limiter.reset_transient_failures(self._api_key)
            try:
                return json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as err:
                raise InvalidProviderResponseError(
                    "NOBIL returned invalid JSON"
                ) from err

    async def _async_request_body(self, parameters: dict[str, str]) -> bytes:
        """Send a key-bearing request in the form body and sanitize failures."""
        try:
            async with self._session.post(
                _API_URL,
                data=parameters,
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("NOBIL rejected the API key")
                if response.status == 429:
                    retry_after = retry_after_seconds(
                        response.headers.get("Retry-After")
                    )
                    self._request_limiter.defer_for_retry_after(
                        self._api_key, retry_after
                    )
                    raise ProviderRateLimitError(
                        "NOBIL rate-limited the request", retry_after
                    )
                if 500 <= response.status < 600:
                    raise ProviderConnectionError(
                        "NOBIL returned a temporary server error"
                    )
                if not 200 <= response.status < 300:
                    raise InvalidProviderResponseError(
                        "NOBIL rejected the search request"
                    )
                return await response.read()
        except (
            AuthenticationError,
            InvalidProviderResponseError,
            ProviderRateLimitError,
            ProviderConnectionError,
        ):
            raise
        except (TimeoutError, aiohttp.ClientError, OSError):
            raise ProviderConnectionError("Could not connect to NOBIL") from None


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
        raise UnsupportedLocationError("The NOBIL search location is invalid")
    if not MIN_SEARCH_RADIUS_M <= radius_m <= MAX_SEARCH_RADIUS_M:
        raise UnsupportedLocationError("The NOBIL search radius is unsupported")
    if not MIN_MAX_STATIONS <= limit <= MAX_MAX_STATIONS:
        raise UnsupportedLocationError("The NOBIL station limit is unsupported")


def _station_records(payload: object) -> list[object]:
    records: object
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        raw_records = payload.get("chargerstations")
        if raw_records is None and "csmd" in payload:
            records = [payload]
        else:
            records = raw_records
    else:
        records = None

    if not isinstance(records, list):
        raise InvalidProviderResponseError(
            "NOBIL returned an invalid charger-station collection"
        )
    return records


def _station_from_record(
    value: object,
    country: _NobilCountry,
    latitude: float,
    longitude: float,
) -> ChargingStation | None:
    record = _mapping(value, "charger station")
    metadata = _mapping(record.get("csmd"), "station metadata")
    station_id = _required_string(
        metadata.get("International_id"), "international station id"
    )
    expected_prefix = f"{country.api_country_code}_"
    if not station_id.upper().startswith(expected_prefix):
        return None

    land_code = metadata.get("Land_code")
    if land_code is not None and (
        not isinstance(land_code, str)
        or land_code.casefold() != country.api_country_code.casefold()
    ):
        return None

    station_latitude, station_longitude = _position(metadata.get("Position"))
    distance = distance_m(latitude, longitude, station_latitude, station_longitude)
    name = _optional_string(metadata.get("name"), "station name")
    address = _address(metadata)
    operator = _optional_string(metadata.get("Owned_by"), "station operator")
    last_updated = _optional_timestamp(metadata.get("Updated"))
    count = _optional_nonnegative_int(
        metadata.get("Number_charging_points"), "charging point count"
    )
    available_count = _optional_nonnegative_int(
        metadata.get("Available_charging_points"), "available charging point count"
    )
    if count is not None and available_count is not None and available_count > count:
        raise InvalidProviderResponseError(
            "NOBIL reported more available points than total points"
        )

    connectors: tuple[ChargingConnector, ...] = ()
    availability_reported = False
    connector_count_reported = count is not None
    if count is not None and count > 0:
        connector_type = _connector_type(record.get("conn"))
        connectors = (
            ChargingConnector(
                provider_connector_id=f"{station_id}:group",
                connector_type=connector_type,
                connector_type_id=_connector_type_id(connector_type),
                status=ConnectorStatus.UNKNOWN,
                max_power_kw=None,
                count=count,
                available_count=available_count,
            ),
        )
    availability_reported = count is not None and available_count is not None

    display_name = name or address or station_id
    location = ChargingLocation(
        provider_id=station_id,
        provider=country.provider_id,
        provider_name=country.provider_name,
        country_code=country.country_code,
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
        distance_m=distance,
        connectors=connectors,
        tariff=None,
        last_updated=last_updated,
        provider=country.provider_id,
        availability_reported=availability_reported,
        is_open=None,
        country_code=country.country_code,
        location=location,
        provider_name=country.provider_name,
        connector_count_reported=connector_count_reported,
        tariff_status=TariffStatus.NOT_REPORTED,
        last_updated_source=(
            ProviderTimestampSource.NOBIL if last_updated is not None else None
        ),
    )


def _mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise InvalidProviderResponseError(f"NOBIL returned invalid {field}")
    return value


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidProviderResponseError(f"NOBIL returned invalid {field}")
    return value.strip()


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidProviderResponseError(f"NOBIL returned invalid {field}")
    return value.strip() or None


def _optional_nonnegative_int(value: object, field: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise InvalidProviderResponseError(f"NOBIL returned invalid {field}")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        try:
            result = int(value)
        except ValueError as err:
            raise InvalidProviderResponseError(
                f"NOBIL returned invalid {field}"
            ) from err
    else:
        raise InvalidProviderResponseError(f"NOBIL returned invalid {field}")
    if result < 0:
        raise InvalidProviderResponseError(f"NOBIL returned invalid {field}")
    return result


def _position(value: object) -> tuple[float, float]:
    if not isinstance(value, str):
        raise InvalidProviderResponseError("NOBIL returned an invalid position")
    match = _POSITION_PATTERN.fullmatch(value)
    if match is None:
        raise InvalidProviderResponseError("NOBIL returned an invalid position")
    latitude, longitude = map(float, match.groups())
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        raise InvalidProviderResponseError("NOBIL returned an invalid position")
    return latitude, longitude


def _address(metadata: dict[str, object]) -> str | None:
    street = _optional_string(metadata.get("Street"), "street")
    house_number = _optional_string(metadata.get("House_number"), "house number")
    postal_code = _optional_string(metadata.get("Zipcode"), "postal code")
    city = _optional_string(metadata.get("City"), "city")
    street_line = " ".join(value for value in (street, house_number) if value)
    locality = " ".join(value for value in (postal_code, city) if value)
    parts = [value for value in (street_line, locality) if value]
    return ", ".join(parts) or None


def _optional_timestamp(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise InvalidProviderResponseError("NOBIL returned an invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise InvalidProviderResponseError(
            "NOBIL returned an invalid timestamp"
        ) from err
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _connector_type(value: object) -> str | None:
    if value is None:
        return None
    attributes = _mapping(value, "connector attributes")
    for attribute in attributes.values():
        item = _mapping(attribute, "connector attribute")
        if _optional_string(item.get("attrname"), "connector attribute name") != (
            "Connector"
        ):
            continue
        return _optional_string(
            item.get("trans"), "connector type"
        ) or _optional_string(item.get("attrval"), "connector type")
    return None


def _connector_type_id(value: str | None) -> ConnectorTypeId | None:
    if value is None:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "", value.casefold())
    if "chademo" in normalized:
        return ConnectorTypeId.CHADEMO
    if "ccs" in normalized or "combo2" in normalized or "t2combo" in normalized:
        return ConnectorTypeId.CCS_COMBO_2
    if "type2" in normalized or "iec62196t2" in normalized:
        return ConnectorTypeId.TYPE_2
    if "single16" in normalized:
        return ConnectorTypeId.CEE_16A_SINGLE_PHASE
    if "three16" in normalized:
        return ConnectorTypeId.CEE_16A_THREE_PHASE
    if "three32" in normalized:
        return ConnectorTypeId.CEE_32A_THREE_PHASE
    return None
