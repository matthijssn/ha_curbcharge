"""NDW DOT-NL client and provider factory."""

import asyncio
import gzip
import hashlib
import json
import logging
import math
import re
import time
import zlib
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal, TypedDict, cast
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import aiohttp
from homeassistant.core import HomeAssistant

from .belgium import create_belgium_provider
from .const import (
    COUNTRY_NL,
    DOMAIN,
    GEOJSON_REQUEST_TIMEOUT,
    MAX_BBOX_AREA_DEG2,
    MIN_SEARCH_RADIUS_M,
    PROVIDER_API_URL,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_NAME,
    PROVIDER_NDW_DOT_NL,
    TARIFF_CACHE_TTL,
    TARIFF_FEED_URL,
    TARIFF_REQUEST_TIMEOUT,
)
from .geography import distance_m
from .models import (
    ChargingConnector,
    ChargingLocation,
    ChargingStation,
    ChargingTariff,
    ConnectorStatus,
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

_EARTH_RADIUS_M = 6_371_000
_METERS_PER_DEGREE = 111_320
_NDW_LOCAL_TIMEZONE = ZoneInfo("Europe/Amsterdam")
_NDW_REQUEST_INTERVAL = 0.11
_NDW_REQUEST_LIMITER_DATA_KEY = "ndw_request_limiter"
_LOGGER = logging.getLogger(__name__)


class GeoJSONFeature(TypedDict):
    """Validated DOT-NL station feature."""

    type: Literal["Feature"]
    id: str
    geometry: dict[str, object]
    properties: dict[str, object]


class GeoJSONFeatureCollection(TypedDict):
    """Validated page of DOT-NL station features."""

    type: Literal["FeatureCollection"]
    features: list[GeoJSONFeature]


class TariffPriceComponent(TypedDict, total=False):
    """OCPI price component fields consumed by CurbCharge."""

    type: str
    price: object
    vat: object
    step_size: object


class TariffElement(TypedDict, total=False):
    """OCPI tariff element fields consumed by CurbCharge."""

    restrictions: object
    price_components: list[TariffPriceComponent]


class TariffRecord(TypedDict, total=False):
    """OCPI tariff fields consumed by CurbCharge."""

    id: object
    type: object
    currency: object
    elements: object
    start_date_time: object
    end_date_time: object
    tax_included: object
    tariff_alt_text: object


class NdwRequestLimiter:
    """Serialize NDW feature requests across CurbCharge entries."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._last_request: float | None = None
        self._retry_after_until = 0.0

    async def async_wait(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                allowed_at = max(now, self._retry_after_until)
                if self._last_request is not None:
                    allowed_at = max(
                        allowed_at, self._last_request + _NDW_REQUEST_INTERVAL
                    )
                delay = allowed_at - now
                if delay > 0:
                    await asyncio.sleep(delay)
                if self._retry_after_until > time.monotonic():
                    continue
                self._last_request = time.monotonic()
                return

    def defer_requests(self, seconds: int | None) -> None:
        """Pause shared feature requests after a provider Retry-After response."""
        if seconds is not None and seconds > 0:
            self._retry_after_until = max(
                self._retry_after_until, time.monotonic() + seconds
            )


def _shared_ndw_request_limiter(hass: HomeAssistant) -> NdwRequestLimiter:
    domain_data = hass.data.setdefault(DOMAIN, {})
    limiter = domain_data.get(_NDW_REQUEST_LIMITER_DATA_KEY)
    if limiter is None:
        limiter = NdwRequestLimiter()
        domain_data[_NDW_REQUEST_LIMITER_DATA_KEY] = limiter
    if not isinstance(limiter, NdwRequestLimiter):
        raise RuntimeError("CurbCharge NDW request limiter has an invalid type")
    return limiter


class NdwDotNlProvider(BaseChargingProvider):
    """Client for NDW's documented DOT-NL GeoJSON API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        request_limiter: NdwRequestLimiter | None = None,
    ) -> None:
        """Initialize the provider with Home Assistant's shared session."""
        self._session = session
        self._request_limiter = request_limiter or NdwRequestLimiter()
        self._tariff_cache: dict[str, ChargingTariff | None] = {}
        self._cached_tariff_ids: set[str] = set()
        self._tariff_cache_updated: datetime | None = None

    @property
    def provider_name(self) -> str:
        """Return the display name of the provider."""
        return PROVIDER_NAME

    @property
    def provider_id(self) -> str:
        """Return the stable NDW DOT-NL provider identifier."""
        return PROVIDER_NDW_DOT_NL

    @property
    def country_code(self) -> str:
        """Return the country served by NDW DOT-NL."""
        return COUNTRY_NL

    async def async_validate_connection(
        self, latitude: float, longitude: float, radius_m: int
    ) -> None:
        """Check the live endpoint and validate its returned feature schema."""
        features = await self._async_get_features(latitude, longitude, radius_m)
        for feature in features:
            _station_from_feature(feature, latitude, longitude)

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
        """Filter nearby NDW stations before the cap, then add saved favourites."""
        features = await self._async_get_features(latitude, longitude, radius_m)
        parsed_stations = [
            _station_from_feature(feature, latitude, longitude) for feature in features
        ]
        discovery_filters = discovery_filters or DiscoveryFilterSettings()
        stations_by_id = {station.provider_id: station for station in parsed_stations}
        nearby = [
            station
            for station in parsed_stations
            if station.distance_m is not None
            and station.distance_m <= radius_m
            and (include_unknown or station.availability_reported)
            and discovery_filters.matches_station(station)
        ]
        nearby.sort(key=lambda station: (station.distance_m, station.provider_id))
        nearby = nearby[:max_stations]
        nearby_ids = {station.provider_id for station in nearby}

        favourite_stations: dict[str, ChargingStation] = {}
        for favourite in favourites:
            if favourite.provider != self.provider_id:
                raise InvalidProviderResponseError(
                    "A saved favourite belongs to a different provider"
                )
            if favourite.provider_id in nearby_ids:
                continue

            station = stations_by_id.get(favourite.provider_id)
            if station is None:
                features_at_favourite = await self._async_get_features(
                    favourite.latitude,
                    favourite.longitude,
                    MIN_SEARCH_RADIUS_M,
                )
                station = next(
                    (
                        parsed
                        for feature in features_at_favourite
                        if (
                            parsed := _station_from_feature(
                                feature, latitude, longitude
                            )
                        ).provider_id
                        == favourite.provider_id
                    ),
                    None,
                )
            if station is not None:
                favourite_stations[favourite.provider_id] = station

        tariff_ids = {
            tariff_id
            for station in (*nearby, *favourite_stations.values())
            for connector in station.connectors
            for tariff_id in connector.tariff_ids
        }
        tariffs = await self._async_get_tariffs(tariff_ids)
        return StationDiscoveryResult(
            nearby=tuple(_with_station_tariff(station, tariffs) for station in nearby),
            favourites=tuple(
                _with_station_tariff(station, tariffs)
                for station in favourite_stations.values()
            ),
        )

    async def _async_get_features(
        self, latitude: float, longitude: float, radius_m: int
    ) -> list[GeoJSONFeature]:
        """Fetch all pages for a bounded search area."""
        bbox = _bounding_box(latitude, longitude, radius_m)
        url = PROVIDER_API_URL
        params: dict[str, str] | None = {"bbox": bbox}
        seen_urls: set[str] = set()
        features: list[GeoJSONFeature] = []
        expected_total: int | None = None

        while url:
            request_url = urljoin(PROVIDER_API_URL, url)
            if request_url in seen_urls:
                raise InvalidProviderResponseError(
                    "NDW DOT-NL returned a pagination loop"
                )
            seen_urls.add(request_url)

            await self._request_limiter.async_wait()
            try:
                body, headers = await self._async_request(
                    request_url, params=params, timeout=GEOJSON_REQUEST_TIMEOUT
                )
            except ProviderRateLimitError as err:
                self._request_limiter.defer_requests(err.retry_after)
                raise
            params = None
            try:
                payload: object = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as err:
                raise InvalidProviderResponseError(
                    "NDW DOT-NL returned invalid GeoJSON"
                ) from err

            feature_collection = _feature_collection(payload)
            features.extend(feature_collection["features"])
            total_header = headers.get("x-total-count")
            if total_header is not None:
                try:
                    expected_total = int(total_header)
                except ValueError as err:
                    raise InvalidProviderResponseError(
                        "NDW DOT-NL returned invalid pagination metadata"
                    ) from err

            next_url = _next_page_url(headers.get("link"), request_url)
            if next_url is None:
                if expected_total is not None and expected_total > len(features):
                    raise InvalidProviderResponseError(
                        "NDW DOT-NL omitted a page of charging stations"
                    )
                break
            url = next_url

        station_ids: set[str] = set()
        for feature in features:
            station_id = feature["id"]
            if station_id in station_ids:
                raise InvalidProviderResponseError(
                    "NDW DOT-NL returned duplicate charging station identifiers"
                )
            station_ids.add(station_id)
        return features

    async def _async_get_tariffs(
        self, tariff_ids: set[str]
    ) -> dict[str, ChargingTariff | None]:
        """Fetch the country-wide OCPI tariff file only when needed."""
        if not tariff_ids:
            return {}

        now = datetime.now(UTC)
        expired = (
            self._tariff_cache_updated is None
            or now - self._tariff_cache_updated >= TARIFF_CACHE_TTL
        )
        if not expired and tariff_ids.issubset(self._cached_tariff_ids):
            return {
                tariff_id: self._tariff_cache[tariff_id] for tariff_id in tariff_ids
            }
        lookup_ids = tariff_ids if expired else tariff_ids | self._cached_tariff_ids

        body, _ = await self._async_request(
            TARIFF_FEED_URL, timeout=TARIFF_REQUEST_TIMEOUT
        )
        try:
            records_value: object = json.loads(gzip.decompress(body))
        except (
            gzip.BadGzipFile,
            EOFError,
            OSError,
            zlib.error,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as err:
            raise InvalidProviderResponseError(
                "NDW DOT-NL returned an invalid tariff file"
            ) from err
        if not isinstance(records_value, list):
            raise InvalidProviderResponseError(
                "NDW DOT-NL returned an invalid tariff list"
            )
        records: list[object] = [record for record in records_value]

        if expired:
            self._tariff_cache.clear()
            self._cached_tariff_ids.clear()

        found: dict[str, ChargingTariff | None] = {}
        for record_value in records:
            if not isinstance(record_value, dict):
                raise InvalidProviderResponseError(
                    "NDW DOT-NL returned an invalid tariff"
                )
            record = _string_mapping(record_value, "tariff")
            record_id = record.get("id")
            if isinstance(record_id, str) and record_id in lookup_ids:
                tariff_id = record_id
                if tariff_id in found:
                    raise InvalidProviderResponseError(
                        "NDW DOT-NL returned a duplicate tariff"
                    )
                found[tariff_id] = _parse_tariff(cast(TariffRecord, record), now=now)

        for tariff_id in lookup_ids:
            self._tariff_cache[tariff_id] = found.get(tariff_id)
        missing_count = len(lookup_ids - found.keys())
        if missing_count:
            _LOGGER.warning(
                "NDW DOT-NL tariff file omitted %s referenced tariff(s); "
                "prices are unknown",
                missing_count,
            )
        self._cached_tariff_ids = lookup_ids
        self._tariff_cache_updated = now
        return {tariff_id: self._tariff_cache[tariff_id] for tariff_id in tariff_ids}

    async def _async_request(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        timeout: timedelta,
    ) -> tuple[bytes, dict[str, str]]:
        """Make one request and translate transport errors without leaking URLs."""
        try:
            async with self._session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout.total_seconds()),
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("NDW DOT-NL rejected authentication")
                if response.status == 429:
                    raise ProviderRateLimitError(
                        "NDW DOT-NL rate limit reached",
                        retry_after_seconds(response.headers.get("Retry-After")),
                    )
                if response.status < 200 or response.status >= 300:
                    raise ProviderConnectionError(
                        "NDW DOT-NL returned an unsuccessful response"
                    )
                return await response.read(), {
                    key.lower(): value for key, value in response.headers.items()
                }
        except (AuthenticationError, ProviderRateLimitError, ProviderConnectionError):
            raise
        except (TimeoutError, aiohttp.ClientError, OSError):
            raise ProviderConnectionError("Could not connect to NDW DOT-NL") from None


def create_provider(
    provider_id: str,
    session: aiohttp.ClientSession,
    hass: HomeAssistant | None = None,
    token: str | None = None,
) -> BaseChargingProvider:
    """Return the requested implemented provider."""
    if provider_id == PROVIDER_NDW_DOT_NL:
        limiter = _shared_ndw_request_limiter(hass) if hass is not None else None
        return NdwDotNlProvider(session, limiter)
    if provider_id == PROVIDER_ECO_MOVEMENT_BE and hass is not None and token:
        return create_belgium_provider(hass, session, token)
    raise UnsupportedProviderError("The configured charging provider is unsupported")


def _feature_collection(payload: object) -> GeoJSONFeatureCollection:
    collection = _string_mapping(payload, "feature collection")
    if collection.get("type") != "FeatureCollection":
        raise InvalidProviderResponseError(
            "NDW DOT-NL returned an invalid GeoJSON feature collection"
        )
    raw_features = collection.get("features")
    if not isinstance(raw_features, list):
        raise InvalidProviderResponseError(
            "NDW DOT-NL returned an invalid charging station"
        )
    features: list[GeoJSONFeature] = []
    for raw_feature in raw_features:
        feature = _string_mapping(raw_feature, "charging station")
        if feature.get("type") != "Feature":
            raise InvalidProviderResponseError("NDW DOT-NL returned an invalid feature")
        features.append(
            {
                "type": "Feature",
                "id": _required_string(feature.get("id"), "station id"),
                "geometry": _string_mapping(feature.get("geometry"), "geometry"),
                "properties": _string_mapping(
                    feature.get("properties"), "station data"
                ),
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _station_from_feature(
    feature: GeoJSONFeature, latitude: float, longitude: float
) -> ChargingStation:
    """Validate one documented GeoJSON feature and map it to a station."""
    if feature.get("type") != "Feature":
        raise InvalidProviderResponseError("NDW DOT-NL returned an invalid feature")
    station_id = _required_string(feature.get("id"), "station id")
    geometry = feature["geometry"]
    if geometry.get("type") != "Point":
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid coordinates")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid coordinates")
    station_longitude = _coordinate_number(coordinates[0], "longitude")
    station_latitude = _coordinate_number(coordinates[1], "latitude")
    if not (-180 <= station_longitude <= 180 and -90 <= station_latitude <= 90):
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid coordinates")

    properties = feature["properties"]
    raw_address = properties.get("address")
    if not isinstance(raw_address, str):
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid address")
    address = raw_address.strip() or None
    _required_string(properties.get("cpo_id"), "CPO identifier")
    is_open = properties.get("open")
    if not isinstance(is_open, bool):
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid open status")
    operator = _optional_string(properties.get("operator_name"), "operator")
    raw_last_updated = properties.get("last_updated")
    last_updated = (
        None if raw_last_updated is None else _parse_datetime(raw_last_updated)
    )

    raw_availabilities = properties.get("availabilities")
    if raw_availabilities is None:
        availability_reported = False
        connectors: tuple[ChargingConnector, ...] = ()
    elif isinstance(raw_availabilities, list):
        availability_reported = True
        connectors = _parse_availabilities(
            station_id, [availability for availability in raw_availabilities]
        )
    else:
        raise InvalidProviderResponseError(
            "NDW DOT-NL returned invalid connector availability"
        )

    location = ChargingLocation(
        provider_id=station_id,
        provider=PROVIDER_NDW_DOT_NL,
        provider_name=PROVIDER_NAME,
        country_code=COUNTRY_NL,
        name=address or station_id,
        latitude=station_latitude,
        longitude=station_longitude,
        address=address,
        operator=operator,
        last_updated=last_updated,
    )
    return ChargingStation(
        provider_id=station_id,
        provider=PROVIDER_NDW_DOT_NL,
        name=location.name,
        latitude=station_latitude,
        longitude=station_longitude,
        address=address,
        operator=operator,
        distance_m=distance_m(latitude, longitude, station_latitude, station_longitude),
        connectors=connectors,
        tariff=None,
        last_updated=last_updated,
        last_updated_source=ProviderTimestampSource.NDW_FEATURE,
        availability_reported=availability_reported,
        is_open=is_open,
        country_code=COUNTRY_NL,
        location=location,
        provider_name=PROVIDER_NAME,
        connector_count_reported=availability_reported,
    )


def _parse_availabilities(
    station_id: str, raw_availabilities: list[object]
) -> tuple[ChargingConnector, ...]:
    parsed: list[tuple[int, int, str, str, str, float, tuple[str, ...]]] = []
    for raw_value in raw_availabilities:
        raw = _string_mapping(raw_value, "connector availability")
        available = _required_integer(raw.get("available"), "available connectors")
        total = _required_integer(raw.get("total"), "total connectors")
        if available > total:
            raise InvalidProviderResponseError(
                "NDW DOT-NL returned inconsistent connector counts"
            )
        connector_type = _required_string(raw.get("connector_type"), "connector type")
        connector_format = _required_string(
            raw.get("connector_format"), "connector format"
        )
        power_type = _required_string(raw.get("power_type"), "power type")
        raw_power = _required_number(raw.get("power_max"), "maximum power")
        # ponytail: live values exceed the OpenAPI unit; revisit when the feed is fixed.
        power_kw = raw_power / 1000 if raw_power > 1000 else raw_power
        raw_tariff_ids = raw.get("tariff_ids")
        if raw_tariff_ids is None:
            tariff_ids: tuple[str, ...] = ()
        elif isinstance(raw_tariff_ids, list) and all(
            isinstance(tariff_id, str) for tariff_id in raw_tariff_ids
        ):
            tariff_ids = tuple(sorted(set(raw_tariff_ids)))
        else:
            raise InvalidProviderResponseError(
                "NDW DOT-NL returned invalid tariff identifiers"
            )
        parsed.append(
            (
                available,
                total,
                connector_type,
                connector_format,
                power_type,
                power_kw,
                tariff_ids,
            )
        )

    parsed.sort(key=lambda row: (row[1], row[2], row[3], row[4], row[5]))
    connectors: list[ChargingConnector] = []
    for (
        available,
        total,
        connector_type,
        connector_format,
        power_type,
        power_kw,
        tariff_ids,
    ) in parsed:
        group_id = hashlib.sha256(
            json.dumps(
                [
                    connector_type,
                    connector_format,
                    power_type,
                    power_kw,
                    tariff_ids,
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:12]
        connectors.append(
            ChargingConnector(
                # ponytail: DOT-NL reports groups and counts, not per-connector IDs.
                provider_connector_id=f"{station_id}:availability:{group_id}",
                connector_type=connector_type,
                status=(
                    ConnectorStatus.AVAILABLE
                    if total == available
                    else ConnectorStatus.UNKNOWN
                ),
                max_power_kw=power_kw,
                count=total,
                available_count=available,
                connector_format=connector_format,
                power_type=power_type,
                tariff_ids=tariff_ids,
            )
        )
    return tuple(connectors)


def _parse_tariff(
    record: TariffRecord, *, now: datetime | None = None
) -> ChargingTariff:
    """Parse an OCPI tariff while preserving unresolved price conditions."""
    currency = _required_string(record.get("currency"), "tariff currency")
    if len(currency) != 3 or not currency.isalpha():
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid currency")
    currency = currency.upper()
    elements = record.get("elements")
    if not isinstance(elements, list):
        raise InvalidProviderResponseError(
            "NDW DOT-NL returned invalid tariff elements"
        )

    now = now or datetime.now(UTC)
    active = _tariff_is_active(record, now)
    tax_included = record.get("tax_included")
    if tax_included is not None:
        if isinstance(tax_included, str):
            tax_included = tax_included.upper()
        if tax_included not in (True, False, "YES", "NO"):
            raise InvalidProviderResponseError(
                "NDW DOT-NL returned invalid tax information"
            )
    parsed_elements: list[tuple[TariffElement, TariffStatus]] = []
    element_values: list[object] = [element for element in elements]
    for element in element_values:
        element_mapping = _string_mapping(element, "tariff element")
        price_components = element_mapping.get("price_components")
        if not isinstance(price_components, list):
            raise InvalidProviderResponseError(
                "NDW DOT-NL returned invalid tariff components"
            )
        component_values: list[object] = [component for component in price_components]
        parsed_components: list[TariffPriceComponent] = []
        for component in component_values:
            component_mapping = _string_mapping(component, "tariff component")
            parsed_components.append(
                {
                    "type": _required_string(
                        component_mapping.get("type"), "tariff component type"
                    ),
                    "price": component_mapping.get("price"),
                    "vat": component_mapping.get("vat"),
                    "step_size": component_mapping.get("step_size"),
                }
            )
        parsed_element: TariffElement = {
            "restrictions": element_mapping.get("restrictions"),
            "price_components": parsed_components,
        }
        parsed_elements.append(
            (
                parsed_element,
                _tariff_element_status(element_mapping.get("restrictions"), now),
            )
        )

    conditional = any(
        status is TariffStatus.CONDITIONAL for _, status in parsed_elements
    )
    active_elements = [
        element
        for element, status in parsed_elements
        if status is TariffStatus.AVAILABLE
    ]
    components = [
        component
        for element in active_elements
        for component in element["price_components"]
    ]

    if not active:
        price_per_kwh = session_fee = price_per_minute = None
        vat_percentage = None
        status = TariffStatus.INACTIVE
    elif conditional:
        price_per_kwh = session_fee = price_per_minute = None
        vat_percentage = None
        status = TariffStatus.CONDITIONAL
    elif not active_elements:
        price_per_kwh = session_fee = price_per_minute = None
        vat_percentage = None
        status = TariffStatus.INACTIVE if parsed_elements else TariffStatus.INCOMPLETE
    else:
        price_per_kwh, vat_percentage = _unique_component_price(
            components, "ENERGY", tax_included
        )
        session_fee, _ = _unique_component_price(components, "FLAT", tax_included)
        price_per_minute = _unique_time_price(components, tax_included)
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
        description=_tariff_description(record.get("tariff_alt_text")),
        source=PROVIDER_NAME,
        vat_percentage=vat_percentage,
        tariff_id=_optional_string(record.get("id"), "tariff id"),
        tariff_type=_optional_string(record.get("type"), "tariff type"),
        status=status,
    )


def _unique_component_price(
    components: list[TariffPriceComponent],
    component_type: str,
    tax_included: bool | str | None,
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
    components: list[TariffPriceComponent], tax_included: bool | str | None
) -> Decimal | None:
    prices: set[Decimal] = set()
    for component in components:
        if component.get("type") != "TIME":
            continue
        step_size = component.get("step_size")
        if (
            not isinstance(step_size, int)
            or isinstance(step_size, bool)
            or step_size <= 0
        ):
            return None
        price, _ = _gross_component_price(component, tax_included)
        if price is None:
            return None
        prices.add(price * Decimal(60) / step_size)
    return next(iter(prices)) if len(prices) == 1 else None


def _gross_component_price(
    component: TariffPriceComponent, tax_included: bool | str | None
) -> tuple[Decimal | None, Decimal | None]:
    price = _decimal_price(component.get("price"))
    raw_vat = component.get("vat")
    vat = _decimal_price(raw_vat) if raw_vat is not None else None
    if tax_included is True or tax_included == "YES":
        return price, vat
    if tax_included in (None, False, "NO") and vat is not None:
        return price * (Decimal(1) + vat / Decimal(100)), vat
    return None, vat


def _decimal_price(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise InvalidProviderResponseError(
            "NDW DOT-NL returned an invalid tariff price"
        )
    try:
        price = Decimal(str(value))
    except InvalidOperation as err:
        raise InvalidProviderResponseError(
            "NDW DOT-NL returned an invalid tariff price"
        ) from err
    if not price.is_finite() or price < 0:
        raise InvalidProviderResponseError(
            "NDW DOT-NL returned an invalid tariff price"
        )
    return price


def _tariff_is_active(record: TariffRecord, now: datetime) -> bool:
    start = record.get("start_date_time")
    end = record.get("end_date_time")
    if start is not None and _parse_datetime(start) > now:
        return False
    if end is not None and _parse_datetime(end) < now:
        return False
    return True


def _tariff_element_status(restrictions: object, now: datetime) -> TariffStatus:
    """Evaluate date-only restrictions and keep session-dependent rates unknown."""
    if restrictions is None:
        return TariffStatus.AVAILABLE

    fields = _string_mapping(restrictions, "tariff restrictions")
    local_date = now.astimezone(_NDW_LOCAL_TIMEZONE).date()
    start_date = fields.get("start_date")
    end_date = fields.get("end_date")
    if start_date is not None and local_date < _tariff_restriction_date(
        start_date, "start date"
    ):
        return TariffStatus.INACTIVE
    if end_date is not None and local_date >= _tariff_restriction_date(
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


def _tariff_restriction_date(value: object, field: str) -> date:
    """Parse an OCPI local calendar date."""
    if not isinstance(value, str):
        raise InvalidProviderResponseError(
            f"NDW DOT-NL returned an invalid tariff {field}"
        )
    try:
        parsed = date.fromisoformat(value)
    except ValueError as err:
        raise InvalidProviderResponseError(
            f"NDW DOT-NL returned an invalid tariff {field}"
        ) from err
    if parsed.isoformat() != value:
        raise InvalidProviderResponseError(
            f"NDW DOT-NL returned an invalid tariff {field}"
        )
    return parsed


def _tariff_description(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    return text
    return None


def _with_station_tariff(
    station: ChargingStation, tariffs: dict[str, ChargingTariff | None]
) -> ChargingStation:
    connectors: list[ChargingConnector] = []
    for connector in station.connectors:
        if not connector.tariff_ids:
            connectors.append(
                replace(
                    connector,
                    tariffs=(),
                    tariff_status=TariffStatus.NOT_REPORTED,
                )
            )
            continue

        records = [tariffs.get(tariff_id) for tariff_id in connector.tariff_ids]
        group_tariffs = tuple(tariff for tariff in records if tariff is not None)
        connectors.append(
            replace(
                connector,
                tariffs=group_tariffs,
                tariff_status=_connector_tariff_status(
                    group_tariffs, missing=len(group_tariffs) != len(records)
                ),
            )
        )

    station_tariff = None
    if not connectors:
        station_status = TariffStatus.NOT_REPORTED
    elif all(
        connector.tariff_status is TariffStatus.AVAILABLE for connector in connectors
    ):
        station_group_tariffs = [connector.tariffs[0] for connector in connectors]
        if all(
            _tariff_signature(tariff) == _tariff_signature(station_group_tariffs[0])
            for tariff in station_group_tariffs[1:]
        ):
            linked_tariffs = [
                tariff for connector in connectors for tariff in connector.tariffs
            ]
            tariff_ids = {tariff.tariff_id for tariff in linked_tariffs}
            descriptions = {tariff.description for tariff in linked_tariffs}
            station_tariff = replace(
                station_group_tariffs[0],
                tariff_id=next(iter(tariff_ids)) if len(tariff_ids) == 1 else None,
                description=(
                    next(iter(descriptions)) if len(descriptions) == 1 else None
                ),
            )
            station_status = TariffStatus.AVAILABLE
        else:
            station_status = TariffStatus.AMBIGUOUS
    else:
        station_status = _combine_tariff_statuses(
            [connector.tariff_status for connector in connectors]
        )

    return replace(
        station,
        connectors=tuple(connectors),
        tariff=station_tariff,
        tariff_status=station_status,
    )


def _connector_tariff_status(
    tariffs: tuple[ChargingTariff, ...], *, missing: bool
) -> TariffStatus:
    if missing or not tariffs:
        return TariffStatus.MISSING

    statuses = {_resolved_tariff_status(tariff) for tariff in tariffs}
    status = _combine_tariff_statuses(list(statuses))
    if status is not TariffStatus.AVAILABLE:
        return status
    if any(
        _tariff_signature(tariff) != _tariff_signature(tariffs[0])
        for tariff in tariffs[1:]
    ):
        return TariffStatus.AMBIGUOUS
    return TariffStatus.AVAILABLE


def _resolved_tariff_status(tariff: ChargingTariff) -> TariffStatus:
    return tariff.effective_status


def _combine_tariff_statuses(
    statuses: list[TariffStatus | None],
) -> TariffStatus:
    known_statuses = {status for status in statuses if status is not None}
    if not known_statuses or known_statuses == {TariffStatus.NOT_REPORTED}:
        return TariffStatus.NOT_REPORTED
    if known_statuses == {TariffStatus.AVAILABLE}:
        return TariffStatus.AVAILABLE
    for status in (
        TariffStatus.CONDITIONAL,
        TariffStatus.INACTIVE,
        TariffStatus.MISSING,
        TariffStatus.AMBIGUOUS,
        TariffStatus.INCOMPLETE,
    ):
        if status in known_statuses:
            return status
    return TariffStatus.INCOMPLETE


def _tariff_signature(tariff: ChargingTariff | None) -> tuple[object, ...] | None:
    if tariff is None:
        return None
    return (
        tariff.currency,
        tariff.price_per_kwh,
        tariff.session_fee,
        tariff.price_per_minute,
        tariff.vat_percentage,
        tariff.tariff_type,
    )


def _bounding_box(latitude: float, longitude: float, radius_m: int) -> str:
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise UnsupportedLocationError(
            "The selected location is outside provider bounds"
        )
    latitude_delta = radius_m / _METERS_PER_DEGREE
    cosine = abs(math.cos(math.radians(latitude)))
    longitude_delta = 180 if cosine < 1e-8 else radius_m / (_METERS_PER_DEGREE * cosine)
    min_latitude = max(-90, latitude - latitude_delta)
    max_latitude = min(90, latitude + latitude_delta)
    min_longitude = longitude - longitude_delta
    max_longitude = longitude + longitude_delta
    if min_longitude < -180 or max_longitude > 180:
        raise UnsupportedLocationError(
            "NDW DOT-NL cannot represent a search area across the date line"
        )
    if (max_longitude - min_longitude) * (
        max_latitude - min_latitude
    ) > MAX_BBOX_AREA_DEG2:
        raise UnsupportedLocationError(
            "The search area exceeds the provider's documented bounding-box limit"
        )
    return ",".join(
        f"{value:.6f}"
        for value in (
            min_longitude,
            min_latitude,
            max_longitude,
            max_latitude,
        )
    )


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise InvalidProviderResponseError("NDW DOT-NL returned an invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as err:
        raise InvalidProviderResponseError(
            "NDW DOT-NL returned an invalid timestamp"
        ) from err
    if parsed.tzinfo is None:
        raise InvalidProviderResponseError("NDW DOT-NL returned an invalid timestamp")
    return parsed.astimezone(UTC)


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field)


def _required_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    return number


def _coordinate_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    return number


def _required_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    return value


def _string_mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
        result[key] = item
    return result


def _next_page_url(link_header: str | None, current_url: str) -> str | None:
    if not link_header:
        return None
    for match in re.finditer(r"<([^>]+)>\s*;([^,]+)", link_header):
        if not re.search(r'\brel="?next"?', match.group(2), re.IGNORECASE):
            continue
        candidate = urljoin(current_url, match.group(1))
        parsed = urlsplit(candidate)
        expected = urlsplit(PROVIDER_API_URL)
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected.hostname
            or parsed.port not in (None, 443)
            or parsed.path != expected.path
        ):
            raise InvalidProviderResponseError(
                "NDW DOT-NL returned an invalid pagination link"
            )
        return str(candidate)
    return None
