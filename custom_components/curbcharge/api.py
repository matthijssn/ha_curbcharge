"""NDW DOT-NL client and provider factory."""

import asyncio
import gzip
import json
import logging
import math
import re
import zlib
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from .const import (
    GEOJSON_REQUEST_TIMEOUT,
    MAX_BBOX_AREA_DEG2,
    PROVIDER_API_URL,
    PROVIDER_NAME,
    PROVIDER_NDW_DOT_NL,
    TARIFF_CACHE_TTL,
    TARIFF_FEED_URL,
    TARIFF_REQUEST_TIMEOUT,
)
from .models import ChargingConnector, ChargingStation, ChargingTariff, ConnectorStatus
from .provider import (
    AuthenticationError,
    BaseChargingProvider,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
    UnsupportedProviderError,
)

_EARTH_RADIUS_M = 6_371_000
_METERS_PER_DEGREE = 111_320
_LOGGER = logging.getLogger(__name__)


class NdwDotNlProvider(BaseChargingProvider):
    """Client for NDW's documented DOT-NL GeoJSON API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the provider with Home Assistant's shared session."""
        self._session = session
        self._tariff_cache: dict[str, ChargingTariff | None] = {}
        self._cached_tariff_ids: set[str] = set()
        self._tariff_cache_updated: datetime | None = None

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
    ) -> tuple[ChargingStation, ...]:
        """Fetch nearby stations and attach only unambiguous published tariffs."""
        features = await self._async_get_features(latitude, longitude, radius_m)
        stations = [
            station
            for feature in features
            if (
                station := _station_from_feature(feature, latitude, longitude)
            ).distance_m
            is not None
            and station.distance_m <= radius_m
        ]
        stations.sort(key=lambda station: (station.distance_m, station.provider_id))
        stations = stations[:max_stations]

        tariff_ids = {
            tariff_id
            for station in stations
            for connector in station.connectors
            for tariff_id in connector.tariff_ids
        }
        tariffs = await self._async_get_tariffs(tariff_ids)
        return tuple(_with_station_tariff(station, tariffs) for station in stations)

    async def _async_get_features(
        self, latitude: float, longitude: float, radius_m: int
    ) -> list[dict[str, Any]]:
        """Fetch all pages for a bounded search area."""
        bbox = _bounding_box(latitude, longitude, radius_m)
        url = PROVIDER_API_URL
        params: dict[str, str] | None = {"bbox": bbox}
        seen_urls: set[str] = set()
        features: list[dict[str, Any]] = []
        expected_total: int | None = None

        while url:
            request_url = urljoin(PROVIDER_API_URL, url)
            if request_url in seen_urls:
                raise InvalidProviderResponseError(
                    "NDW DOT-NL returned a pagination loop"
                )
            seen_urls.add(request_url)

            body, headers = await self._async_request(
                request_url, params=params, timeout=GEOJSON_REQUEST_TIMEOUT
            )
            params = None
            try:
                payload = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError) as err:
                raise InvalidProviderResponseError(
                    "NDW DOT-NL returned invalid GeoJSON"
                ) from err

            page_features = _feature_collection_features(payload)
            features.extend(page_features)
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
            await asyncio.sleep(0.11)
            url = next_url

        station_ids: set[str] = set()
        for feature in features:
            station_id = feature.get("id")
            if not isinstance(station_id, str):
                raise InvalidProviderResponseError(
                    "NDW DOT-NL returned an invalid station identifier"
                )
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
            records = json.loads(gzip.decompress(body))
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
        if not isinstance(records, list):
            raise InvalidProviderResponseError(
                "NDW DOT-NL returned an invalid tariff list"
            )

        if expired:
            self._tariff_cache.clear()
            self._cached_tariff_ids.clear()

        found: dict[str, ChargingTariff | None] = {}
        for record in records:
            if (
                isinstance(record, dict)
                and isinstance(record.get("id"), str)
                and record["id"] in lookup_ids
            ):
                tariff_id = record["id"]
                if tariff_id in found:
                    raise InvalidProviderResponseError(
                        "NDW DOT-NL returned a duplicate tariff"
                    )
                found[tariff_id] = _parse_tariff(record)

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
        timeout: Any,
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
                        _retry_after_seconds(response.headers.get("Retry-After")),
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
    provider_id: str, session: aiohttp.ClientSession
) -> BaseChargingProvider:
    """Return the requested implemented provider."""
    if provider_id == PROVIDER_NDW_DOT_NL:
        return NdwDotNlProvider(session)
    raise UnsupportedProviderError("The configured charging provider is unsupported")


def _feature_collection_features(payload: Any) -> list[dict[str, Any]]:
    if (
        not isinstance(payload, dict)
        or payload.get("type") != "FeatureCollection"
        or not isinstance(payload.get("features"), list)
    ):
        raise InvalidProviderResponseError(
            "NDW DOT-NL returned an invalid GeoJSON feature collection"
        )
    if not all(isinstance(feature, dict) for feature in payload["features"]):
        raise InvalidProviderResponseError(
            "NDW DOT-NL returned an invalid charging station"
        )
    return payload["features"]


def _station_from_feature(
    feature: dict[str, Any], latitude: float, longitude: float
) -> ChargingStation:
    """Validate one documented GeoJSON feature and map it to a station."""
    if feature.get("type") != "Feature":
        raise InvalidProviderResponseError("NDW DOT-NL returned an invalid feature")
    station_id = _required_string(feature.get("id"), "station id")
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid coordinates")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid coordinates")
    station_longitude = _coordinate_number(coordinates[0], "longitude")
    station_latitude = _coordinate_number(coordinates[1], "latitude")
    if not (-180 <= station_longitude <= 180 and -90 <= station_latitude <= 90):
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid coordinates")

    properties = feature.get("properties")
    if not isinstance(properties, dict):
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid station data")
    raw_address = properties.get("address")
    if not isinstance(raw_address, str):
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid address")
    address = raw_address.strip() or None
    _required_string(properties.get("cpo_id"), "CPO identifier")
    is_open = properties.get("open")
    if not isinstance(is_open, bool):
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid open status")
    operator = _optional_string(properties.get("operator_name"), "operator")
    last_updated = _parse_datetime(properties.get("last_updated"))

    raw_availabilities = properties.get("availabilities")
    if raw_availabilities is None:
        availability_reported = False
        connectors: tuple[ChargingConnector, ...] = ()
    elif isinstance(raw_availabilities, list):
        availability_reported = True
        connectors = _parse_availabilities(station_id, raw_availabilities)
    else:
        raise InvalidProviderResponseError(
            "NDW DOT-NL returned invalid connector availability"
        )

    return ChargingStation(
        provider_id=station_id,
        provider=PROVIDER_NDW_DOT_NL,
        name=address or station_id,
        latitude=station_latitude,
        longitude=station_longitude,
        address=address,
        operator=operator,
        distance_m=_distance_m(
            latitude, longitude, station_latitude, station_longitude
        ),
        connectors=connectors,
        tariff=None,
        last_updated=last_updated,
        availability_reported=availability_reported,
        is_open=is_open,
    )


def _parse_availabilities(
    station_id: str, raw_availabilities: list[Any]
) -> tuple[ChargingConnector, ...]:
    parsed: list[tuple[dict[str, Any], str, str, str, float, tuple[str, ...]]] = []
    for raw in raw_availabilities:
        if not isinstance(raw, dict):
            raise InvalidProviderResponseError(
                "NDW DOT-NL returned invalid connector availability"
            )
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
                raw,
                connector_type,
                connector_format,
                power_type,
                power_kw,
                tariff_ids,
            )
        )

    parsed.sort(key=lambda row: (row[1], row[2], row[3], row[4], row[5]))
    connectors: list[ChargingConnector] = []
    for index, (
        raw,
        connector_type,
        connector_format,
        power_type,
        power_kw,
        tariff_ids,
    ) in enumerate(parsed):
        available = int(raw["available"])
        total = int(raw["total"])
        connectors.append(
            ChargingConnector(
                # ponytail: DOT-NL reports groups and counts, not per-connector IDs.
                provider_connector_id=f"{station_id}:availability:{index}",
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


def _parse_tariff(record: dict[str, Any]) -> ChargingTariff:
    """Parse an OCPI tariff conservatively; conditional prices remain unknown."""
    currency = _required_string(record.get("currency"), "tariff currency")
    if len(currency) != 3 or not currency.isalpha():
        raise InvalidProviderResponseError("NDW DOT-NL returned invalid currency")
    currency = currency.upper()
    elements = record.get("elements")
    if not isinstance(elements, list):
        raise InvalidProviderResponseError(
            "NDW DOT-NL returned invalid tariff elements"
        )

    active = _tariff_is_active(record, datetime.now(UTC))
    tax_included = record.get("tax_included")
    if tax_included is not None:
        if isinstance(tax_included, str):
            tax_included = tax_included.upper()
        if tax_included not in (True, False, "YES", "NO"):
            raise InvalidProviderResponseError(
                "NDW DOT-NL returned invalid tax information"
            )
    restricted = False
    components: list[dict[str, Any]] = []
    for element in elements:
        if not isinstance(element, dict):
            raise InvalidProviderResponseError("NDW DOT-NL returned an invalid tariff")
        if element.get("restrictions"):
            restricted = True
        price_components = element.get("price_components")
        if not isinstance(price_components, list):
            raise InvalidProviderResponseError(
                "NDW DOT-NL returned invalid tariff components"
            )
        for component in price_components:
            if not isinstance(component, dict):
                raise InvalidProviderResponseError(
                    "NDW DOT-NL returned an invalid tariff component"
                )
            components.append(component)

    if active and not restricted:
        price_per_kwh, vat_percentage = _unique_component_price(
            components, "ENERGY", tax_included
        )
        session_fee, _ = _unique_component_price(components, "FLAT", tax_included)
        price_per_minute = _unique_time_price(components, tax_included)
    else:
        price_per_kwh = session_fee = price_per_minute = None
        vat_percentage = None

    return ChargingTariff(
        currency=currency,
        price_per_kwh=price_per_kwh,
        session_fee=session_fee,
        price_per_minute=price_per_minute,
        description=_tariff_description(record.get("tariff_alt_text")),
        source=PROVIDER_NAME,
        vat_percentage=vat_percentage,
    )


def _unique_component_price(
    components: list[dict[str, Any]],
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
    components: list[dict[str, Any]], tax_included: bool | str | None
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
    component: dict[str, Any], tax_included: bool | str | None
) -> tuple[Decimal | None, Decimal | None]:
    price = _decimal_price(component.get("price"))
    raw_vat = component.get("vat")
    vat = _decimal_price(raw_vat) if raw_vat is not None else None
    if tax_included is True or tax_included == "YES":
        return price, vat
    if tax_included in (None, False, "NO") and vat is not None:
        return price * (Decimal(1) + vat / Decimal(100)), vat
    return None, vat


def _decimal_price(value: Any) -> Decimal:
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


def _tariff_is_active(record: dict[str, Any], now: datetime) -> bool:
    start = record.get("start_date_time")
    end = record.get("end_date_time")
    if start is not None and _parse_datetime(start) > now:
        return False
    if end is not None and _parse_datetime(end) < now:
        return False
    return True


def _tariff_description(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                return item["text"]
    return None


def _with_station_tariff(
    station: ChargingStation, tariffs: dict[str, ChargingTariff | None]
) -> ChargingStation:
    groups: list[ChargingTariff] = []
    for connector in station.connectors:
        if not connector.tariff_ids:
            return station
        group_tariffs = [tariffs.get(tariff_id) for tariff_id in connector.tariff_ids]
        if not group_tariffs or any(tariff is None for tariff in group_tariffs):
            return station
        first = group_tariffs[0]
        if any(
            _tariff_signature(tariff) != _tariff_signature(first)
            for tariff in group_tariffs[1:]
        ):
            return station
        groups.append(first)
    if not groups or any(
        _tariff_signature(tariff) != _tariff_signature(groups[0])
        for tariff in groups[1:]
    ):
        return station
    return replace(station, tariff=groups[0])


def _tariff_signature(tariff: ChargingTariff | None) -> tuple[Any, ...] | None:
    if tariff is None:
        return None
    return (
        tariff.currency,
        tariff.price_per_kwh,
        tariff.session_fee,
        tariff.price_per_minute,
        tariff.vat_percentage,
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


def _distance_m(
    latitude: float, longitude: float, other_latitude: float, other_longitude: float
) -> int:
    """Calculate distance using the haversine formula."""
    lat1, lat2 = math.radians(latitude), math.radians(other_latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(other_longitude - longitude)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return round(2 * _EARTH_RADIUS_M * math.asin(math.sqrt(haversine)))


def _parse_datetime(value: Any) -> datetime:
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


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    return value


def _optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field)


def _required_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    return number


def _coordinate_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    return number


def _required_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InvalidProviderResponseError(f"NDW DOT-NL returned invalid {field}")
    return value


def _retry_after_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0, math.ceil((retry_at - datetime.now(UTC)).total_seconds()))


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
        return candidate
    return None
