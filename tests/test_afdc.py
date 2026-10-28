"""Tests for the U.S. AFDC/NLR station provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from homeassistant.core import HomeAssistant

import custom_components.curbcharge.afdc as afdc
from custom_components.curbcharge.api import create_provider
from custom_components.curbcharge.const import PROVIDER_AFDC_US
from custom_components.curbcharge.models import (
    ConnectorStatus,
    ConnectorTypeId,
    FavouriteStationReference,
    ProviderTimestampSource,
    TariffStatus,
)
from custom_components.curbcharge.provider import (
    AuthenticationError,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
)


class FakeResponse:
    """Minimal response supporting aiohttp's async context-manager interface."""

    def __init__(
        self,
        payload: object | bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def read(self) -> bytes:
        return self._body


class FakeSession:
    """Capture API requests and return queued fake responses."""

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, str], object]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        timeout: object,
    ) -> FakeResponse:
        self.requests.append((url, params, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _sample_payload() -> dict[str, Any]:
    path = Path(__file__).parent / "fixtures" / "afdc_stations_synthetic.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _sample_record() -> dict[str, Any]:
    return _sample_payload()["fuel_stations"][0]


def _provider(
    session: FakeSession,
    hass: HomeAssistant | None = None,
) -> afdc.AFDCProvider:
    provider = create_provider(PROVIDER_AFDC_US, session, hass, "synthetic-afdc-key")
    assert isinstance(provider, afdc.AFDCProvider)
    return provider


@pytest.mark.asyncio
async def test_afdc_maps_connector_counts_and_leaves_availability_unknown():
    session = FakeSession([FakeResponse(_sample_payload())])
    provider = _provider(session)

    result = await provider.async_get_stations(39.743078, -105.152278, 10_000, 10)

    assert provider.provider_id == PROVIDER_AFDC_US
    assert provider.country_code == "US"
    assert len(result.nearby) == 1
    station = result.nearby[0]
    assert station.provider_id == "990001"
    assert station.country_code == "US"
    assert station.name == "Synthetic Public Charging Station"
    assert station.address == "100 Example Avenue, Denver CO 80202"
    assert station.operator == "Synthetic Network"
    assert station.distance_m == 0
    assert station.is_open is True
    assert station.connector_count_reported
    assert station.total_connectors == 5
    assert not station.availability_reported
    assert station.available_connectors is None
    assert station.has_available_connectors is None
    assert station.last_updated == datetime(2026, 10, 28, 12, tzinfo=UTC)
    assert station.last_updated_source is ProviderTimestampSource.AFDC
    assert station.tariff is None
    assert station.tariff_status is TariffStatus.NOT_REPORTED
    assert [connector.connector_type_id for connector in station.connectors] == [
        ConnectorTypeId.TYPE_1,
        ConnectorTypeId.CCS_COMBO_1,
    ]
    assert all(
        connector.status is ConnectorStatus.UNKNOWN for connector in station.connectors
    )
    assert all(connector.available_count is None for connector in station.connectors)

    url, parameters, _timeout = session.requests[0]
    assert url == afdc._NEAREST_API_URL
    assert parameters["api_key"] == "synthetic-afdc-key"
    assert parameters["country"] == "US"
    assert parameters["fuel_type"] == "ELEC"
    assert parameters["access"] == "public"
    assert parameters["latitude"] == "39.743078"
    assert parameters["longitude"] == "-105.152278"
    assert parameters["radius"] == f"{10_000 / afdc._METERS_PER_MILE:.6f}"


@pytest.mark.asyncio
async def test_afdc_fetches_saved_favourites_by_station_id(monkeypatch):
    payload = _sample_payload()
    session = FakeSession(
        [
            FakeResponse({"total_results": 0, "fuel_stations": []}),
            FakeResponse(payload),
        ]
    )
    provider = _provider(session)

    async def no_wait(_api_key: str) -> None:
        return None

    monkeypatch.setattr(provider._request_limiter, "async_wait", no_wait)
    favourite = FavouriteStationReference(
        provider=PROVIDER_AFDC_US,
        provider_id="990001",
        name="990001",
        latitude=39.743078,
        longitude=-105.152278,
    )

    result = await provider.async_get_stations(
        40.0, -105.0, 100, 10, favourites=(favourite,)
    )

    assert result.nearby == ()
    assert [station.provider_id for station in result.favourites] == ["990001"]
    url, parameters, _timeout = session.requests[1]
    assert url == afdc._ALL_API_URL
    assert parameters["ids"] == "990001"
    assert parameters["limit"] == "all"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "is_open"),
    (("E", True), ("P", False), ("T", False), ("future", None)),
)
async def test_afdc_station_status_does_not_infer_connector_availability(
    status_code, is_open
):
    payload = _sample_payload()
    payload["fuel_stations"][0]["status_code"] = status_code
    provider = _provider(FakeSession([FakeResponse(payload)]))

    result = await provider.async_get_stations(39.743078, -105.152278, 100, 10)

    station = result.nearby[0]
    assert station.is_open is is_open
    assert not station.availability_reported
    assert station.available_connectors is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "headers", "error_type", "retry_after"),
    [
        (401, {}, AuthenticationError, None),
        (403, {}, AuthenticationError, None),
        (429, {"Retry-After": "60"}, ProviderRateLimitError, 60),
        (503, {}, ProviderConnectionError, None),
        (400, {}, InvalidProviderResponseError, None),
    ],
)
async def test_afdc_maps_http_errors_without_exposing_credentials(
    status, headers, error_type, retry_after
):
    provider = _provider(
        FakeSession([FakeResponse({}, status=status, headers=headers)])
    )

    with pytest.raises(error_type) as error:
        await provider.async_validate_connection(39.743078, -105.152278, 100)

    assert "synthetic-afdc-key" not in str(error.value)
    if isinstance(error.value, ProviderRateLimitError):
        assert error.value.retry_after == retry_after


@pytest.mark.asyncio
async def test_afdc_rejects_malformed_station_collection():
    provider = _provider(FakeSession([FakeResponse({"fuel_stations": {}})]))

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_validate_connection(39.743078, -105.152278, 100)


def test_afdc_rejects_empty_or_nonprintable_api_keys():
    with pytest.raises(ValueError):
        afdc.create_afdc_provider(FakeSession([]), "bad\nkey")


def test_afdc_shares_request_limiter_per_home_assistant(hass: HomeAssistant):
    provider = _provider(FakeSession([]), hass)
    another_entry_provider = _provider(FakeSession([]), hass)

    assert provider.provider_name == afdc.PROVIDER_AFDC_US_NAME
    assert provider._request_limiter is another_entry_provider._request_limiter

    hass.data[afdc.DOMAIN][afdc._REQUEST_LIMITER_DATA_KEY] = object()
    with pytest.raises(RuntimeError):
        _provider(FakeSession([]), hass)


@pytest.mark.asyncio
async def test_afdc_request_limiter_enforces_interval_and_retry_after(monkeypatch):
    clock = [100.0]
    delays: list[float] = []

    async def advance_time(seconds: float) -> None:
        delays.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(afdc, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(afdc.asyncio, "sleep", advance_time)
    limiter = afdc.AFDCRequestLimiter()

    await limiter.async_wait("first-key")
    limiter.defer_for_retry_after("first-key", None)
    limiter.defer_for_retry_after("first-key", 0)
    limiter.defer_for_retry_after("first-key", -1)
    limiter.defer_for_retry_after("first-key", 5)
    await limiter.async_wait("other-key")
    await limiter.async_wait("first-key")
    await limiter.async_wait("first-key")

    assert delays == pytest.approx([5, afdc._REQUEST_INTERVAL_SECONDS])


@pytest.mark.asyncio
async def test_afdc_validates_connection_from_a_station_record():
    provider = _provider(FakeSession([FakeResponse(_sample_payload())]))

    await provider.async_validate_connection(39.743078, -105.152278, 100)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(float("nan"), 0.0), (True, 0.0), (91.0, 0.0), (0.0, 181.0)],
)
@pytest.mark.asyncio
async def test_afdc_rejects_invalid_search_coordinates(latitude, longitude):
    provider = _provider(FakeSession([]))

    with pytest.raises(UnsupportedLocationError):
        await provider.async_validate_connection(latitude, longitude, 100)


@pytest.mark.asyncio
async def test_afdc_rejects_unsupported_search_radius_and_station_limit():
    provider = _provider(FakeSession([]))

    with pytest.raises(UnsupportedLocationError):
        await provider.async_validate_connection(39.743078, -105.152278, 0)
    with pytest.raises(UnsupportedLocationError):
        await provider.async_get_stations(39.743078, -105.152278, 100, 0)


@pytest.mark.asyncio
async def test_afdc_rejects_favourites_from_another_provider():
    provider = _provider(FakeSession([]))
    favourite = FavouriteStationReference(
        provider="another_provider",
        provider_id="123",
        name="Station",
        latitude=39.743078,
        longitude=-105.152278,
    )

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_get_stations(
            39.743078, -105.152278, 100, 10, favourites=(favourite,)
        )


@pytest.mark.parametrize(
    "transport_error",
    [TimeoutError("timeout"), aiohttp.ClientError("offline"), OSError("offline")],
)
@pytest.mark.asyncio
async def test_afdc_maps_transport_errors(transport_error):
    provider = _provider(FakeSession([transport_error]))

    with pytest.raises(ProviderConnectionError):
        await provider.async_validate_connection(39.743078, -105.152278, 100)


@pytest.mark.parametrize("body", [b"not json", b"\xff"])
@pytest.mark.asyncio
async def test_afdc_rejects_invalid_json(body):
    provider = _provider(FakeSession([FakeResponse(body)]))

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_validate_connection(39.743078, -105.152278, 100)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fuel_type_code", "GAS"),
        ("country", "CA"),
        ("access_code", None),
        ("access_code", "private"),
    ],
)
@pytest.mark.asyncio
async def test_afdc_ignores_non_public_us_electric_stations(field, value):
    record = _sample_record()
    record[field] = value
    provider = _provider(FakeSession([FakeResponse({"fuel_stations": [record]})]))

    result = await provider.async_get_stations(39.743078, -105.152278, 100, 10)

    assert result.nearby == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", True),
        ("id", -1),
        ("latitude", True),
        ("latitude", float("nan")),
        ("latitude", 91),
        ("longitude", 181),
        ("station_name", 123),
        ("updated_at", "not-a-timestamp"),
    ],
)
@pytest.mark.asyncio
async def test_afdc_rejects_invalid_station_fields(field, value):
    record = _sample_record()
    record[field] = value
    provider = _provider(FakeSession([FakeResponse({"fuel_stations": [record]})]))

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_validate_connection(39.743078, -105.152278, 100)


@pytest.mark.asyncio
async def test_afdc_uses_station_id_when_optional_metadata_is_missing():
    record = _sample_record()
    record.update(
        {
            "station_name": " ",
            "street_address": None,
            "city": None,
            "state": None,
            "zip": None,
            "ev_network": None,
            "updated_at": None,
        }
    )
    provider = _provider(FakeSession([FakeResponse({"fuel_stations": [record]})]))

    result = await provider.async_get_stations(39.743078, -105.152278, 100, 10)

    station = result.nearby[0]
    assert station.name == "990001"
    assert station.address is None
    assert station.operator is None
    assert station.last_updated is None
    assert station.last_updated_source is None


@pytest.mark.asyncio
async def test_afdc_ignores_naive_station_update_timestamps():
    record = _sample_record()
    record["updated_at"] = "2026-10-28T12:00:00"
    provider = _provider(FakeSession([FakeResponse({"fuel_stations": [record]})]))

    result = await provider.async_get_stations(39.743078, -105.152278, 100, 10)

    assert result.nearby[0].last_updated is None
    assert result.nearby[0].last_updated_source is None


@pytest.mark.asyncio
async def test_afdc_maps_aggregate_connector_counts_when_units_are_unavailable():
    record = _sample_record()
    record["ev_charging_units"] = []
    record["ev_level1_evse_num"] = 0
    record["ev_level2_evse_num"] = 2
    record["ev_dc_fast_num"] = 1
    provider = _provider(FakeSession([FakeResponse({"fuel_stations": [record]})]))

    result = await provider.async_get_stations(39.743078, -105.152278, 100, 10)

    station = result.nearby[0]
    assert station.connector_count_reported
    assert station.total_connectors == 3
    assert [connector.power_type for connector in station.connectors] == ["AC", "DC"]


@pytest.mark.asyncio
async def test_afdc_reports_incomplete_fallback_connector_counts():
    record = _sample_record()
    record["ev_charging_units"] = [{"connectors": None}]
    for field, _level, _power_type in afdc._FALLBACK_LEVELS:
        record[field] = None
    provider = _provider(FakeSession([FakeResponse({"fuel_stations": [record]})]))

    result = await provider.async_get_stations(39.743078, -105.152278, 100, 10)

    station = result.nearby[0]
    assert station.connectors == ()
    assert not station.connector_count_reported


@pytest.mark.asyncio
async def test_afdc_preserves_unknown_connector_types_and_missing_counts():
    record = _sample_record()
    record["ev_charging_units"] = [
        {
            "charging_level": "future_level",
            "connectors": {
                "FUTURE": {"port_count": 1, "power_kw": None},
                "ZERO": {"port_count": 0},
                "MISSING": {},
            },
        },
        {"connectors": None},
    ]
    provider = _provider(FakeSession([FakeResponse({"fuel_stations": [record]})]))

    result = await provider.async_get_stations(39.743078, -105.152278, 100, 10)

    station = result.nearby[0]
    assert len(station.connectors) == 1
    assert station.connectors[0].connector_type == "FUTURE"
    assert station.connectors[0].connector_type_id is None
    assert station.connectors[0].power_type is None
    assert not station.connector_count_reported


@pytest.mark.parametrize(
    "units",
    [
        {},
        [None],
        [{"connectors": []}],
        [{"connectors": {"J1772": None}}],
    ],
)
@pytest.mark.asyncio
async def test_afdc_rejects_malformed_charging_units(units):
    record = _sample_record()
    record["ev_charging_units"] = units
    provider = _provider(FakeSession([FakeResponse({"fuel_stations": [record]})]))

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_validate_connection(39.743078, -105.152278, 100)


@pytest.mark.parametrize(
    ("field", "value"),
    [("port_count", -1), ("port_count", True), ("power_kw", -1)],
)
@pytest.mark.asyncio
async def test_afdc_rejects_invalid_connector_values(field, value):
    record = _sample_record()
    connector = record["ev_charging_units"][0]["connectors"]["J1772"]
    connector[field] = value
    provider = _provider(FakeSession([FakeResponse({"fuel_stations": [record]})]))

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_validate_connection(39.743078, -105.152278, 100)
