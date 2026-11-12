"""Offline tests for the Open Charge Map directory adapter."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest

from custom_components.curbcharge.const import PROVIDER_OPEN_CHARGE_MAP
from custom_components.curbcharge.models import (
    ConnectorStatus,
    ConnectorTypeId,
    FavouriteStationReference,
    ProviderTimestampSource,
    TariffStatus,
)
from custom_components.curbcharge.providers import create_provider
from custom_components.curbcharge.providers import openchargemap as ocm
from custom_components.curbcharge.providers.base import (
    AuthenticationError,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
)


def test_factory_requires_key_and_constructs_global_provider():
    with pytest.raises(AuthenticationError, match="Open Charge Map"):
        create_provider(PROVIDER_OPEN_CHARGE_MAP, Session([]))
    provider = create_provider(
        PROVIDER_OPEN_CHARGE_MAP, Session([]), token="synthetic-key"
    )
    assert provider.provider_id == PROVIDER_OPEN_CHARGE_MAP
    assert provider.country_code is None


def record(station_id: int = 123, latitude: float = 51.0) -> dict[str, Any]:
    return {
        "ID": station_id,
        "UsageTypeID": 1,
        "DataProvider": {
            "Title": "Synthetic contributor",
            "WebsiteURL": "https://example.org/source",
            "License": "Synthetic open license",
            "IsOpenDataLicensed": True,
        },
        "AddressInfo": {
            "Title": "Synthetic station",
            "Latitude": latitude,
            "Longitude": 4.0,
            "AddressLine1": "Example street",
            "Town": "Example",
            "Country": {"ISOCode": "be"},
        },
        "OperatorID": 9,
        "OperatorInfo": {"Title": "Synthetic operator"},
        "DateLastStatusUpdate": "2026-01-02T03:04:05Z",
        "NumberOfPoints": 99,
        "UsageCost": "Free",
        "StatusType": {"IsOperational": True},
        "Connections": [
            {
                "ID": 22,
                "ConnectionTypeID": 25,
                "ConnectionType": {"Title": "Type 2"},
                "Quantity": 2,
                "PowerKW": 22,
                "CurrentTypeID": 20,
                "StatusTypeID": 50,
            }
        ],
    }


class Content:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def iter_chunked(self, size: int):
        for offset in range(0, len(self.body), size):
            yield self.body[offset : offset + size]


class Response:
    def __init__(
        self,
        payload: object = (),
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = Content(
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )

    async def __aenter__(self):
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *_args: object) -> None:
        pass


class Session:
    def __init__(self, responses: list[Response | Exception]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> Response:
        self.requests.append({"url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def provider(session: Session, hass: Any = None, key: str = "synthetic-key"):
    return ocm.create_openchargemap_provider(session, key, hass)


def favourite(station_id: str) -> FavouriteStationReference:
    return FavouriteStationReference(
        provider=PROVIDER_OPEN_CHARGE_MAP,
        provider_id=station_id,
        name=station_id,
        latitude=51.0,
        longitude=4.0,
    )


@pytest.mark.asyncio
async def test_mapping_and_query():
    session = Session([Response([record()])])
    adapter = provider(session)
    result = await adapter.async_get_stations(51, 4, 1000, 10)
    station = result.nearby[0]
    assert adapter.provider_id == PROVIDER_OPEN_CHARGE_MAP
    assert adapter.provider_name == "Open Charge Map (global)"
    assert adapter.country_code is None
    assert station.provider_id == "123"
    assert station.location.country_code == station.country_code == "BE"
    assert station.distance_m == 0
    assert station.address == "Example street, Example"
    assert station.operator == "Synthetic operator"
    assert station.operator_id == "9"
    assert station.source_name == "Synthetic contributor"
    assert station.source_url == "https://example.org/source"
    assert station.source_license == "Synthetic open license"
    assert station.last_updated == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert station.last_updated_source is ProviderTimestampSource.OPEN_CHARGE_MAP
    assert station.total_connectors == 2
    assert not station.availability_reported and station.available_connectors is None
    assert station.is_open is None
    assert station.tariff is None and station.tariff_status is TariffStatus.NOT_REPORTED
    connector = station.connectors[0]
    assert connector.provider_connector_id == "123:22"
    assert connector.connector_type_id is ConnectorTypeId.TYPE_2
    assert (
        connector.status is ConnectorStatus.UNKNOWN
        and connector.available_count is None
    )
    assert connector.max_power_kw == 22 and connector.power_type == "AC"
    request = session.requests[0]
    assert request["url"] == ocm._URL and not request["allow_redirects"]
    assert request["headers"]["X-API-Key"] == "synthetic-key"
    assert "synthetic-key" not in repr(request["params"])
    assert request["params"] == {
        "output": "json",
        "maxresults": "500",
        "opendata": "true",
        "usagetypeid": "1,4,5,7",
        "includecomments": "false",
        "compact": "false",
        "verbose": "true",
        "camelcase": "false",
        "latitude": "51.0",
        "longitude": "4.0",
        "distance": "1.0",
        "distanceunit": "km",
    }


@pytest.mark.parametrize(
    ("ocm_id", "expected"),
    list(ocm._CONNECTOR_TYPES.items()) + [(9999, None)],
)
def test_official_connector_ids(ocm_id, expected):
    item = record()
    item["Connections"][0]["ConnectionTypeID"] = ocm_id
    assert ocm._station(item, 51, 4).connectors[0].connector_type_id == expected


@pytest.mark.parametrize("usage", [0, 2, 3, 6, 8, None])
def test_excludes_nonpublic_or_unknown(usage):
    item = record()
    item["UsageTypeID"] = usage
    assert ocm._station(item, 51, 4) is None


@pytest.mark.parametrize("usage", [1, 4, 5, 7])
def test_accepts_verified_public_usage(usage):
    item = record()
    item["UsageTypeID"] = usage
    assert ocm._station(item, 51, 4) is not None


@pytest.mark.parametrize("source", [None, {}, {"IsOpenDataLicensed": 1}])
def test_invalid_license_shape_fails(source):
    item = record()
    item["DataProvider"] = source
    with pytest.raises(InvalidProviderResponseError):
        ocm._station(item, 51, 4)


@pytest.mark.parametrize("usage", [True, [], "1"])
def test_invalid_usage_shape_fails(usage):
    item = record()
    item["UsageTypeID"] = usage
    with pytest.raises(InvalidProviderResponseError):
        ocm._station(item, 51, 4)


def test_closed_license_is_excluded():
    item = record()
    item["DataProvider"]["IsOpenDataLicensed"] = False
    assert ocm._station(item, 51, 4) is None


def test_unknown_counts_and_untrusted_fields():
    item = record()
    item["Connections"][0].update(Quantity=None, PowerKW=-1, CurrentTypeID=30)
    item["DateLastStatusUpdate"] = "not a timestamp"
    item["DataProvider"]["WebsiteURL"] = "javascript:alert(1)"
    item["AddressInfo"]["Country"]["ISOCode"] = "not a country"
    station = ocm._station(item, 51, 4)
    assert station.total_connectors is None
    assert station.connectors[0].count == 0
    assert station.connectors[0].max_power_kw is None
    assert station.connectors[0].power_type == "DC"
    assert station.last_updated is None and station.last_updated_source is None
    assert station.country_code is None and station.source_url is None


def test_optional_fields_and_malformed_source_url():
    item = record()
    item["DataProvider"]["WebsiteURL"] = "https://[invalid"
    item["DateLastStatusUpdate"] = None
    item["AddressInfo"].update(Title="", Country=None)
    item["Connections"][0]["ConnectionTypeID"] = True
    station = ocm._station(item, 51, 4)
    assert station.name == "123"
    assert station.source_url is None and station.country_code is None
    assert station.last_updated is None
    assert station.connectors[0].connector_type_id is None
    assert ocm._number(10**400) is None
    assert ocm._id("00042") == "42"


@pytest.mark.parametrize("quantity", [None, -1, True, "2", 1.5])
def test_ambiguous_quantities_not_invented(quantity):
    item = record()
    item["Connections"][0]["Quantity"] = quantity
    assert ocm._station(item, 51, 4).total_connectors is None


@pytest.mark.asyncio
async def test_favourites_filtering_sorting_and_cross_border():
    near, far, outside, private = record(1, 51.001), record(2), record(3, 52), record(4)
    near["AddressInfo"]["Country"]["ISOCode"] = "NL"
    private["UsageTypeID"] = 2
    session = Session(
        [Response([near, far, near, outside, private]), Response([outside, private])]
    )
    result = await provider(session).async_get_stations(
        51, 4, 1000, 10, (favourite("3"), favourite("4"), favourite("3"))
    )
    assert [s.provider_id for s in result.nearby] == ["2", "1"]
    assert [s.country_code for s in result.nearby] == ["BE", "NL"]
    assert [s.provider_id for s in result.favourites] == ["3"]
    params = session.requests[1]["params"]
    assert params["chargepointid"] == "3,4" and "latitude" not in params


@pytest.mark.asyncio
async def test_unknown_policy_and_favourite_in_nearby():
    session = Session([Response([record()])])
    result = await provider(session).async_get_stations(
        51, 4, 1000, 10, (favourite("123"),), include_unknown=False
    )
    assert len(result.nearby) == 1 and not result.favourites
    result = await provider(Session([Response([record()])])).async_get_stations(
        51, 4, 1000, 10, include_unknown=False
    )
    assert not result.nearby


@pytest.mark.asyncio
async def test_favourite_batches(monkeypatch):
    monkeypatch.setattr(ocm, "_MAX_RESULTS", 2)
    monkeypatch.setattr(ocm, "_MAX_ID_BATCH", 2)
    session = Session(
        [Response([]), Response([record(1), record(2)]), Response([record(3)])]
    )
    result = await provider(session).async_get_stations(
        51, 4, 1000, 10, tuple(favourite(str(i)) for i in (1, 2, 3))
    )
    assert [s.provider_id for s in result.favourites] == ["1", "2", "3"]
    assert [r["params"].get("chargepointid") for r in session.requests] == [
        None,
        "1,2",
        "3",
    ]


@pytest.mark.asyncio
async def test_cached_favourite_distance_uses_current_origin():
    session = Session([Response([]), Response([record(7, 52)]), Response([])])
    adapter = provider(session)
    first = await adapter.async_get_stations(51, 4, 1000, 10, (favourite("7"),))
    second = await adapter.async_get_stations(50, 4, 1000, 10, (favourite("7"),))
    assert second.favourites[0].distance_m > first.favourites[0].distance_m
    assert len(session.requests) == 3


@pytest.mark.asyncio
async def test_shared_cache_validation_singleflight_and_expiry(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(ocm.time, "monotonic", lambda: clock[0])
    hass = SimpleNamespace(data={})
    session = Session(
        [Response([record()]), Response([record()]), Response([]), Response([])]
    )
    first, second = provider(session, hass), provider(session, hass)
    await asyncio.gather(
        first.async_validate_connection(51, 4, 1000),
        second.async_validate_connection(51.0, 4.0, 1000),
    )
    assert len(session.requests) == 1
    assert (await second.async_get_stations(51, 4, 1000, 10)).nearby
    clock[0] += 3600
    await first.async_validate_connection(51, 4, 1000)
    await provider(session, hass, "another-key").async_validate_connection(51, 4, 1000)
    await first.async_validate_connection(52, 4, 1000)
    assert len(session.requests) == 4


@pytest.mark.asyncio
async def test_singleflight_shares_failures_without_caching_them():
    hass = SimpleNamespace(data={})
    session = Session([Response(status=401), Response([])])
    first, second = provider(session, hass), provider(session, hass)
    failures = await asyncio.gather(
        first.async_validate_connection(51, 4, 1000),
        second.async_validate_connection(51, 4, 1000),
        return_exceptions=True,
    )
    assert all(isinstance(error, AuthenticationError) for error in failures)
    assert len(session.requests) == 1
    await first.async_validate_connection(51, 4, 1000)
    assert len(session.requests) == 2


@pytest.mark.asyncio
async def test_cancelling_waiter_does_not_cancel_shared_request():
    hass = SimpleNamespace(data={})
    session = Session([Response([record()])])
    first, second = provider(session, hass), provider(session, hass)
    waiter = asyncio.create_task(first.async_validate_connection(51, 4, 1000))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await second.async_validate_connection(51, 4, 1000)
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_bounded_cache_and_truncation(monkeypatch):
    monkeypatch.setattr(ocm, "_CACHE_SIZE", 1)
    monkeypatch.setattr(ocm, "_MAX_RESULTS", 2)
    session = Session([Response([record(1), record(2)]), Response([]), Response([])])
    adapter = provider(session)
    result = await adapter.async_get_stations(51, 4, 1000, 10)
    assert result.results_may_be_truncated
    await adapter.async_validate_connection(52, 4, 1000)
    await adapter.async_validate_connection(51, 4, 1000)
    assert len(session.requests) == 3 and len(adapter._cache.entries) == 1


@pytest.mark.asyncio
async def test_rate_limit_shared_per_key(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(ocm.time, "monotonic", lambda: clock[0])
    hass = SimpleNamespace(data={})
    session = Session(
        [
            Response(status=429, headers={"Retry-After": "30"}),
            Response([]),
            Response([]),
        ]
    )
    adapter = provider(session, hass)
    with pytest.raises(ProviderRateLimitError) as error:
        await adapter.async_validate_connection(51, 4, 1000)
    assert error.value.retry_after == 30
    with pytest.raises(ProviderRateLimitError) as error:
        await provider(session, hass).async_validate_connection(52, 4, 1000)
    assert error.value.retry_after == 30 and len(session.requests) == 1
    await provider(session, hass, "different").async_validate_connection(51, 4, 1000)
    clock[0] += 30
    await adapter.async_validate_connection(51, 4, 1000)
    assert len(session.requests) == 3


@pytest.mark.asyncio
async def test_cooldown_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(ocm, "_CACHE_SIZE", 1)
    hass = SimpleNamespace(data={})
    session = Session(
        [
            Response(status=429, headers={"Retry-After": "30"}),
            Response(status=429, headers={"Retry-After": "30"}),
        ]
    )
    for key in ("first", "second"):
        with pytest.raises(ProviderRateLimitError):
            await provider(session, hass, key).async_validate_connection(51, 4, 1000)
    assert len(provider(session, hass)._cache.cooldowns) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (Response(status=401), AuthenticationError),
        (Response(status=403), AuthenticationError),
        (Response(status=302), InvalidProviderResponseError),
        (Response(status=400), InvalidProviderResponseError),
        (Response(status=500), ProviderConnectionError),
        (Response(b"secret invalid json"), InvalidProviderResponseError),
        (Response({}), InvalidProviderResponseError),
        (Response([{}]), InvalidProviderResponseError),
        (
            Response([{"UsageTypeID": 1, "DataProvider": {}}]),
            InvalidProviderResponseError,
        ),
        (
            Response(
                [{"UsageTypeID": 1, "DataProvider": {"IsOpenDataLicensed": True}}]
            ),
            InvalidProviderResponseError,
        ),
        (aiohttp.ClientConnectionError("synthetic-key"), ProviderConnectionError),
        (TimeoutError("synthetic-key"), ProviderConnectionError),
    ],
)
async def test_errors_are_sanitized_and_not_cached(response, error):
    session = Session([response, Response([])])
    adapter = provider(session)
    with pytest.raises(error) as caught:
        await adapter.async_validate_connection(51, 4, 1000)
    assert "synthetic-key" not in str(caught.value)
    assert "secret invalid json" not in str(caught.value)
    await adapter.async_validate_connection(51, 4, 1000)
    assert len(session.requests) == 2


@pytest.mark.asyncio
async def test_body_and_result_bound(monkeypatch):
    monkeypatch.setattr(ocm, "_MAX_BODY", 5)
    with pytest.raises(InvalidProviderResponseError):
        await provider(Session([Response(b"123456")])).async_validate_connection(
            51, 4, 1000
        )
    monkeypatch.setattr(ocm, "_MAX_BODY", 10000)
    monkeypatch.setattr(ocm, "_MAX_RESULTS", 1)
    with pytest.raises(InvalidProviderResponseError):
        await provider(Session([Response([{}, {}])])).async_validate_connection(
            51, 4, 1000
        )


@pytest.mark.parametrize("key", [None, "", " ", "new\nline", "accent-é"])
def test_invalid_api_key(key):
    assert not ocm.is_valid_openchargemap_api_key(key)
    with pytest.raises(ValueError):
        provider(Session([]), key=key)


def test_invalid_shared_cache():
    hass = SimpleNamespace(data={ocm.DOMAIN: {ocm._DATA_KEY: object()}})
    with pytest.raises(RuntimeError):
        provider(Session([]), hass)


@pytest.mark.parametrize(
    ("latitude", "longitude", "radius"),
    [
        (True, 4, 1000),
        (91, 4, 1000),
        (51, float("nan"), 1000),
        (51, 181, 1000),
        (51, 4, 0),
        (51, 4, True),
    ],
)
@pytest.mark.asyncio
async def test_invalid_search_location(latitude, longitude, radius):
    with pytest.raises(UnsupportedLocationError):
        await provider(Session([])).async_validate_connection(
            latitude, longitude, radius
        )


@pytest.mark.asyncio
async def test_invalid_limits_and_favourites():
    adapter = provider(Session([]))
    with pytest.raises(UnsupportedLocationError):
        await adapter.async_get_stations(51, 4, 1000, True)
    with pytest.raises(InvalidProviderResponseError):
        await adapter.async_get_stations(51, 4, 1000, 10, (favourite("bad"),))
    other = deepcopy(favourite("123"))
    object.__setattr__(other, "provider", "another")
    with pytest.raises(InvalidProviderResponseError):
        await adapter.async_get_stations(51, 4, 1000, 10, (other,))


@pytest.mark.parametrize("bad_id", [None, True, 0, -1, "０", "x", "1" * 5000, 10**20])
def test_invalid_id(bad_id):
    with pytest.raises(InvalidProviderResponseError):
        ocm._id(bad_id)


@pytest.mark.parametrize(
    "value",
    [None, [], {"Latitude": True, "Longitude": 4}, {"Latitude": 91, "Longitude": 4}],
)
def test_invalid_address(value):
    item = record()
    item["AddressInfo"] = value
    with pytest.raises(InvalidProviderResponseError):
        ocm._station(item, 51, 4)


def test_invalid_connections_and_duplicate_ids():
    item = record()
    item["Connections"] = {}
    with pytest.raises(InvalidProviderResponseError):
        ocm._station(item, 51, 4)
    item["Connections"] = [record()["Connections"][0]] * 2
    with pytest.raises(InvalidProviderResponseError):
        ocm._station(item, 51, 4)
    item["Connections"] = None
    assert ocm._station(item, 51, 4).total_connectors is None
