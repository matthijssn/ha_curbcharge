"""Tests for the shared NOBIL API v3 client."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

import custom_components.curbcharge.nobil as nobil
from custom_components.curbcharge.api import create_provider
from custom_components.curbcharge.const import (
    PROVIDER_NOBIL_NO,
    PROVIDER_NOBIL_SE,
)
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
    UnsupportedProviderError,
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
    """Capture requests and return queued fake responses."""

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, str], object]] = []

    def post(self, url: str, *, data: dict[str, str], timeout: object) -> FakeResponse:
        self.requests.append((url, data, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _sample_payload() -> dict[str, Any]:
    fixture_path = Path(__file__).parent / "fixtures" / "nobil_stations_synthetic.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


def _station_record(
    station_id: str = "NOR_99992",
    *,
    land_code: str = "nor",
    position: str = "(61.12345,10.98765)",
    connector: str | None = None,
    **metadata_fields: object,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "International_id": station_id,
        "Land_code": land_code,
        "name": "Synthetic station",
        "Position": position,
        "Number_charging_points": 2,
        "Available_charging_points": 1,
    }
    metadata.update(metadata_fields)
    record: dict[str, object] = {"csmd": metadata}
    if connector is not None:
        record["conn"] = {
            "4": {"attrname": "Connector", "trans": connector, "attrval": ""}
        }
    return record


def _provider(
    provider_id: str,
    session: FakeSession,
    hass: HomeAssistant | None = None,
) -> nobil.NobilProvider:
    provider = create_provider(provider_id, session, hass, "synthetic-test-key")
    assert isinstance(provider, nobil.NobilProvider)
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "provider_id",
        "provider_name",
        "country_code",
        "latitude",
        "longitude",
        "station_id",
        "connector_type_id",
        "total_count",
        "available_count",
        "last_updated",
        "timestamp_source",
    ),
    [
        (
            PROVIDER_NOBIL_NO,
            "NOBIL Norway",
            "NO",
            61.12345,
            10.98765,
            "NOR_99991",
            ConnectorTypeId.TYPE_2,
            4,
            2,
            None,
            None,
        ),
        (
            PROVIDER_NOBIL_SE,
            "NOBIL Sweden",
            "SE",
            59.98765,
            18.76543,
            "SWE_99991",
            ConnectorTypeId.CCS_COMBO_2,
            2,
            1,
            datetime(2026, 8, 16, 12, tzinfo=UTC),
            ProviderTimestampSource.NOBIL,
        ),
    ],
)
async def test_nobil_maps_country_station_and_documented_search(
    provider_id,
    provider_name,
    country_code,
    latitude,
    longitude,
    station_id,
    connector_type_id,
    total_count,
    available_count,
    last_updated,
    timestamp_source,
):
    session = FakeSession([FakeResponse(_sample_payload())])
    provider = _provider(provider_id, session)

    result = await provider.async_get_stations(latitude, longitude, 100, 10)

    assert provider.provider_id == provider_id
    assert provider.provider_name == provider_name
    assert provider.country_code == country_code
    assert [station.provider_id for station in result.nearby] == [station_id]
    station = result.nearby[0]
    assert station.country_code == country_code
    assert station.provider == provider_id
    assert station.distance_m == 0
    assert station.connector_count_reported
    assert station.availability_reported
    assert station.total_connectors == total_count
    assert station.available_connectors == available_count
    assert station.connectors[0].status is ConnectorStatus.UNKNOWN
    assert station.connectors[0].connector_type_id is connector_type_id
    assert station.last_updated == last_updated
    assert station.last_updated_source is timestamp_source
    assert station.tariff is None
    assert station.tariff_status is TariffStatus.NOT_REPORTED

    url, parameters, _timeout = session.requests[0]
    assert url == nobil._API_URL
    assert "?" not in url
    assert parameters == {
        "apikey": "synthetic-test-key",
        "apiversion": "3",
        "action": "search",
        "type": "near",
        "lat": f"{latitude:.6f}",
        "long": f"{longitude:.6f}",
        "distance": "100",
        "limit": "50",
        "format": "json",
    }


@pytest.mark.asyncio
async def test_nobil_missing_availability_and_timestamp_remain_unknown():
    record = _sample_payload()["chargerstations"][0]
    record["csmd"].pop("Available_charging_points")
    record["csmd"].pop("Updated")
    session = FakeSession([FakeResponse({"chargerstations": [record]})])
    provider = _provider(PROVIDER_NOBIL_NO, session)

    result = await provider.async_get_stations(61.12345, 10.98765, 100, 10)

    station = result.nearby[0]
    assert not station.availability_reported
    assert station.available_connectors is None
    assert station.has_available_connectors is None
    assert station.last_updated is None
    assert station.last_updated_source is None
    assert station.tariff is None
    assert station.tariff_status is TariffStatus.NOT_REPORTED


@pytest.mark.asyncio
async def test_nobil_refreshes_missing_favourite_using_its_saved_coordinates():
    favourite_latitude = 60.1234
    favourite_longitude = 10.4567
    favourite_record = {
        "csmd": {
            "International_id": "NOR_99992",
            "Land_code": "nor",
            "name": "Synthetic favourite",
            "Position": f"({favourite_latitude},{favourite_longitude})",
            "Number_charging_points": 2,
        }
    }
    session = FakeSession(
        [
            FakeResponse({"chargerstations": []}),
            FakeResponse({"chargerstations": [favourite_record]}),
        ]
    )
    provider = _provider(PROVIDER_NOBIL_NO, session)
    favourite = FavouriteStationReference(
        provider=PROVIDER_NOBIL_NO,
        provider_id="NOR_99992",
        name="NOR_99992",
        latitude=favourite_latitude,
        longitude=favourite_longitude,
    )

    result = await provider.async_get_stations(
        61.12345,
        10.98765,
        100,
        1,
        favourites=(favourite,),
        include_unknown=False,
    )

    assert result.nearby == ()
    assert [station.provider_id for station in result.favourites] == ["NOR_99992"]
    assert len(session.requests) == 2
    assert session.requests[1][1]["lat"] == f"{favourite_latitude:.6f}"
    assert session.requests[1][1]["long"] == f"{favourite_longitude:.6f}"


@pytest.mark.asyncio
async def test_nobil_rejects_malformed_response_and_over_limit_payload():
    invalid_root = _provider(
        PROVIDER_NOBIL_NO,
        FakeSession([FakeResponse(None)]),
    )
    with pytest.raises(InvalidProviderResponseError):
        await invalid_root.async_validate_connection(61.12345, 10.98765, 500)

    malformed = _provider(
        PROVIDER_NOBIL_NO,
        FakeSession([FakeResponse({"chargerstations": {}})]),
    )
    with pytest.raises(InvalidProviderResponseError):
        await malformed.async_validate_connection(61.12345, 10.98765, 500)

    over_limit = _provider(
        PROVIDER_NOBIL_NO,
        FakeSession([FakeResponse({"chargerstations": [None] * 2001})]),
    )
    with pytest.raises(InvalidProviderResponseError, match="response limit"):
        await over_limit.async_validate_connection(61.12345, 10.98765, 500)


@pytest.mark.asyncio
async def test_nobil_authentication_failure_is_not_retried_or_exposed():
    session = FakeSession([FakeResponse({}, status=403)])
    provider = _provider(PROVIDER_NOBIL_NO, session)

    with pytest.raises(AuthenticationError) as raised:
        await provider.async_validate_connection(61.12345, 10.98765, 500)

    assert len(session.requests) == 1
    assert "synthetic-test-key" not in str(raised.value)


@pytest.mark.asyncio
async def test_nobil_honors_retry_after_and_shares_cooldown_by_api_key(
    hass, monkeypatch
):
    current_time = [0.0]
    waits: list[float] = []

    def monotonic() -> float:
        return current_time[0]

    async def sleep(delay: float) -> None:
        waits.append(delay)
        current_time[0] += delay

    monkeypatch.setattr(nobil.time, "monotonic", monotonic)
    monkeypatch.setattr(nobil.asyncio, "sleep", sleep)
    first_session = FakeSession(
        [
            FakeResponse(
                {},
                status=429,
                headers={"Retry-After": "75"},
            )
        ]
    )
    second_session = FakeSession([])
    first = _provider(PROVIDER_NOBIL_NO, first_session, hass)
    second = _provider(PROVIDER_NOBIL_SE, second_session, hass)

    with pytest.raises(ProviderRateLimitError) as raised:
        await first.async_validate_connection(61.12345, 10.98765, 500)
    await second.async_validate_connection(59.98765, 18.76543, 500)

    assert raised.value.retry_after == 75
    assert len(first_session.requests) == 1
    assert len(second_session.requests) == 1
    assert first._request_limiter is second._request_limiter
    api_key_hash = hashlib.sha256(b"synthetic-test-key").digest()
    assert first._request_limiter._retry_after_until[api_key_hash] == 75
    assert current_time[0] == 75
    assert waits == [75]


@pytest.mark.asyncio
async def test_nobil_retries_transient_errors_with_bounded_delays(monkeypatch):
    sleeps: list[int] = []

    async def fake_sleep(seconds: int) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(nobil.asyncio, "sleep", fake_sleep)
    session = FakeSession(
        [
            FakeResponse({}, status=500),
            FakeResponse({}, status=502),
            FakeResponse({"chargerstations": []}),
        ]
    )
    provider = _provider(PROVIDER_NOBIL_NO, session)

    await provider.async_validate_connection(61.12345, 10.98765, 500)

    assert len(session.requests) == 3
    assert sleeps == [1, 2]


@pytest.mark.asyncio
async def test_nobil_stops_after_bounded_transient_retries(monkeypatch):
    sleeps: list[int] = []

    monkeypatch.setattr(nobil.time, "monotonic", lambda: 0.0)

    async def fake_sleep(seconds: int) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(nobil.asyncio, "sleep", fake_sleep)
    session = FakeSession(
        [
            FakeResponse({}, status=500),
            FakeResponse({}, status=500),
            FakeResponse({}, status=500),
        ]
    )
    provider = _provider(PROVIDER_NOBIL_NO, session)

    with pytest.raises(ProviderConnectionError):
        await provider.async_validate_connection(61.12345, 10.98765, 500)

    assert len(session.requests) == 3
    assert sleeps == [1, 2]
    api_key_hash = hashlib.sha256(b"synthetic-test-key").digest()
    assert provider._request_limiter._retry_after_until[api_key_hash] == 30


def test_nobil_transient_cooldown_grows_to_a_five_minute_cap(monkeypatch):
    monkeypatch.setattr(nobil.time, "monotonic", lambda: 0.0)
    limiter = nobil.NobilRequestLimiter()
    api_key = "synthetic-test-key"
    api_key_hash = hashlib.sha256(api_key.encode()).digest()

    for expected_seconds in (30, 60, 120, 240, 300, 300):
        limiter.defer_for_transient_failure(api_key)
        assert limiter._retry_after_until[api_key_hash] == expected_seconds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("latitude", "longitude", "radius_m", "max_stations"),
    [
        (float("nan"), 10.0, 500, 10),
        (61.0, 181.0, 500, 10),
        (61.0, 10.0, 99, 10),
        (61.0, 10.0, 500, 51),
    ],
)
async def test_nobil_rejects_invalid_search_values_before_request(
    latitude, longitude, radius_m, max_stations
):
    session = FakeSession([])
    provider = _provider(PROVIDER_NOBIL_NO, session)

    with pytest.raises(nobil.UnsupportedLocationError):
        await provider.async_get_stations(latitude, longitude, radius_m, max_stations)

    assert session.requests == []


@pytest.mark.asyncio
async def test_nobil_rejects_favourites_for_a_different_country_provider():
    session = FakeSession([])
    provider = _provider(PROVIDER_NOBIL_NO, session)
    favourite = FavouriteStationReference(
        provider=PROVIDER_NOBIL_SE,
        provider_id="SWE_12345",
        name="SWE_12345",
        latitude=59.98,
        longitude=18.76,
    )

    with pytest.raises(InvalidProviderResponseError, match="different NOBIL"):
        await provider.async_get_stations(
            61.12345, 10.98765, 500, 10, favourites=(favourite,)
        )

    assert session.requests == []


@pytest.mark.asyncio
async def test_nobil_accepts_unwrapped_station_shapes_and_filters_land_code():
    record = _station_record()
    list_provider = _provider(
        PROVIDER_NOBIL_NO,
        FakeSession([FakeResponse([record])]),
    )
    single_provider = _provider(
        PROVIDER_NOBIL_NO,
        FakeSession([FakeResponse(record)]),
    )

    await list_provider.async_validate_connection(61.12345, 10.98765, 500)
    await single_provider.async_validate_connection(61.12345, 10.98765, 500)

    wrong_country_provider = _provider(
        PROVIDER_NOBIL_NO,
        FakeSession(
            [
                FakeResponse(
                    {
                        "chargerstations": [
                            _station_record(land_code="swe"),
                        ]
                    }
                )
            ]
        ),
    )
    result = await wrong_country_provider.async_get_stations(
        61.12345, 10.98765, 500, 10
    )
    assert result.nearby == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_stations", "nearby_ids", "favourite_ids"),
    [
        (1, ["NOR_00001"], ["NOR_00002"]),
        (2, ["NOR_00001", "NOR_00002"], []),
    ],
)
async def test_nobil_keeps_favourite_separate_from_nearby_cap(
    max_stations, nearby_ids, favourite_ids
):
    latitude = 61.12345
    longitude = 10.98765
    session = FakeSession(
        [
            FakeResponse(
                {
                    "chargerstations": [
                        _station_record("NOR_00001"),
                        _station_record("NOR_00002", Available_charging_points=None),
                    ]
                }
            )
        ]
    )
    provider = _provider(PROVIDER_NOBIL_NO, session)
    favourite = FavouriteStationReference(
        provider=PROVIDER_NOBIL_NO,
        provider_id="NOR_00002",
        name="NOR_00002",
        latitude=latitude,
        longitude=longitude,
    )

    result = await provider.async_get_stations(
        latitude,
        longitude,
        100,
        max_stations,
        favourites=(favourite,),
        include_unknown=False,
    )

    assert [station.provider_id for station in result.nearby] == nearby_ids
    assert [station.provider_id for station in result.favourites] == favourite_ids
    if favourite_ids:
        assert not result.favourites[0].availability_reported
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_nobil_filters_by_radius_and_selects_nearest_stations():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "chargerstations": [
                        _station_record("NOR_00003", position="(61.13,10.98765)"),
                        _station_record("NOR_00002", position="(61.1236,10.98765)"),
                        _station_record("NOR_00001"),
                    ]
                }
            )
        ]
    )
    provider = _provider(PROVIDER_NOBIL_NO, session)

    result = await provider.async_get_stations(61.12345, 10.98765, 100, 1)

    assert [station.provider_id for station in result.nearby] == ["NOR_00001"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        None,
        {1: "not a station mapping"},
        {"csmd": None},
        {"csmd": {1: "not metadata"}},
        {"csmd": {"International_id": ""}},
        {"csmd": {"International_id": "NOR_99992"}},
        _station_record(position="not a coordinate"),
        _station_record(position="(91,10)"),
        _station_record(Owned_by=1),
        _station_record(Number_charging_points=-1),
        _station_record(Number_charging_points=True),
        _station_record(Number_charging_points=1.5),
        _station_record(Number_charging_points="bad"),
        _station_record(
            Number_charging_points=1,
            Available_charging_points=2,
        ),
        _station_record(Updated=1),
        _station_record(Updated="not a timestamp"),
        {
            **_station_record(),
            "conn": {"4": None},
        },
        {
            **_station_record(),
            "conn": {"4": {"attrname": 1}},
        },
    ],
)
async def test_nobil_rejects_malformed_station_records(record):
    provider = _provider(
        PROVIDER_NOBIL_NO,
        FakeSession([FakeResponse({"chargerstations": [record]})]),
    )

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_validate_connection(61.12345, 10.98765, 500)


@pytest.mark.asyncio
async def test_nobil_formats_address_fallback_and_zero_connector_count():
    first = _station_record(
        "NOR_00003",
        name="",
        Street="Syntheticgata",
        House_number="3",
        Zipcode="0003",
        City="Testby",
        Number_charging_points=0,
        Available_charging_points=0,
    )
    second = _station_record(
        "NOR_00004",
        name="",
        Number_charging_points=None,
        Available_charging_points=None,
        Updated=None,
    )
    third = _station_record("NOR_00005")
    third["conn"] = {
        "1": {"attrname": "Accessability", "trans": "Open"},
        "4": {"attrname": "Connector", "trans": "", "attrval": "CHAdeMO"},
    }
    fourth = _station_record("NOR_00006")
    fourth["conn"] = {"1": {"attrname": "Accessability", "trans": "Open"}}
    provider = _provider(
        PROVIDER_NOBIL_NO,
        FakeSession(
            [FakeResponse({"chargerstations": [first, second, third, fourth]})]
        ),
    )

    result = await provider.async_get_stations(61.12345, 10.98765, 100, 10)

    by_id = {station.provider_id: station for station in result.nearby}
    assert by_id["NOR_00003"].name == "Syntheticgata 3, 0003 Testby"
    assert by_id["NOR_00003"].total_connectors == 0
    assert by_id["NOR_00003"].available_connectors == 0
    assert by_id["NOR_00004"].name == "NOR_00004"
    assert by_id["NOR_00004"].total_connectors is None
    assert by_id["NOR_00005"].connectors[0].connector_type == "CHAdeMO"
    assert by_id["NOR_00005"].connectors[0].connector_type_id is ConnectorTypeId.CHADEMO
    assert by_id["NOR_00006"].connectors[0].connector_type is None
    assert by_id["NOR_00006"].connectors[0].connector_type_id is None


@pytest.mark.parametrize(
    ("connector_type", "expected"),
    [
        ("CHAdeMO", ConnectorTypeId.CHADEMO),
        ("CCS Combo 2", ConnectorTypeId.CCS_COMBO_2),
        ("IEC_62196_T2", ConnectorTypeId.TYPE_2),
        ("IEC_60309_2_SINGLE_16", ConnectorTypeId.CEE_16A_SINGLE_PHASE),
        ("IEC_60309_2_THREE_16", ConnectorTypeId.CEE_16A_THREE_PHASE),
        ("IEC_60309_2_THREE_32", ConnectorTypeId.CEE_32A_THREE_PHASE),
        ("unknown type", None),
        (None, None),
    ],
)
def test_nobil_maps_supported_connector_type_ids(connector_type, expected):
    assert nobil._connector_type_id(connector_type) is expected


@pytest.mark.asyncio
async def test_nobil_retries_connection_errors_and_rejects_invalid_json(
    monkeypatch,
):
    sleeps: list[int] = []

    async def fake_sleep(seconds: int) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(nobil.asyncio, "sleep", fake_sleep)
    session = FakeSession(
        [
            TimeoutError("private request details"),
            FakeResponse({"chargerstations": []}),
        ]
    )
    provider = _provider(PROVIDER_NOBIL_NO, session)
    await provider.async_validate_connection(61.12345, 10.98765, 500)
    assert len(session.requests) == 2
    assert sleeps == [1]

    invalid_json = _provider(
        PROVIDER_NOBIL_NO,
        FakeSession([FakeResponse(b"{")]),
    )
    with pytest.raises(InvalidProviderResponseError, match="invalid JSON"):
        await invalid_json.async_validate_connection(61.12345, 10.98765, 500)


@pytest.mark.asyncio
async def test_nobil_rejects_other_client_errors_without_retry():
    session = FakeSession([FakeResponse({}, status=400)])
    provider = _provider(PROVIDER_NOBIL_NO, session)

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_validate_connection(61.12345, 10.98765, 500)

    assert len(session.requests) == 1


def test_nobil_factory_validates_provider_key_and_shared_limiter(hass):
    with pytest.raises(AuthenticationError):
        create_provider(PROVIDER_NOBIL_NO, FakeSession([]))
    with pytest.raises(UnsupportedProviderError):
        nobil.create_nobil_provider(FakeSession([]), "nobil_fi", "valid-key")
    with pytest.raises(ValueError, match="printable"):
        create_provider(PROVIDER_NOBIL_NO, FakeSession([]), hass, "bad\nkey")

    hass.data.setdefault(nobil.DOMAIN, {})[nobil._REQUEST_LIMITER_DATA_KEY] = object()
    with pytest.raises(RuntimeError, match="NOBIL request limiter"):
        create_provider(PROVIDER_NOBIL_NO, FakeSession([]), hass, "valid-key")
