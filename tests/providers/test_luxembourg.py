"""Offline tests for Chargy's synthetic KML snapshots."""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from xml.sax.saxutils import escape

import aiohttp
import pytest

import custom_components.curbcharge.providers.luxembourg as luxembourg
from custom_components.curbcharge.const import (
    COUNTRY_LU,
    DOMAIN,
    PROVIDER_CHARGY_LU,
)
from custom_components.curbcharge.models import (
    ConnectorStatus,
    FavouriteStationReference,
    TariffStatus,
)
from custom_components.curbcharge.providers import create_provider
from custom_components.curbcharge.providers.base import (
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
)

LUXEMBOURG = (49.61, 6.13)


class FakeResponse:
    """Only expose safe response fields used by the adapter."""

    def __init__(self, body=b"", status=200, headers=None):
        self._body = body
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self):
        await asyncio.sleep(0)
        return self._body

    @property
    def url(self):
        raise AssertionError("The keyed redirect URL must not be accessed")


class FakeSession:
    """Queue fake responses and capture only the keyless request URL."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def get(self, url, *, headers, timeout):
        self.requests.append((url, headers, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _connector(identifier=101, socket=1, status="AVAILABLE", power=22.0):
    return {
        "id": identifier,
        "name": f"CP1 - {socket}",
        "connector": socket,
        "description": status,
        "maxchspeed": power,
    }


def _device(identifier=1, connectors=None):
    connectors = connectors if connectors is not None else [_connector()]
    return {
        "id": identifier,
        "name": f"CP{identifier}",
        "numberOfConnectors": len(connectors),
        "connectors": connectors,
    }


def _placemark(devices=None, coordinates="6.13,49.61,0", name="Synthetic site"):
    devices = devices if devices is not None else [_device()]
    records = "".join(
        '<Data name="chargingdevice"><value>'
        f"{escape(json.dumps(device))}</value></Data>"
        for device in devices
    )
    return (
        f"<Placemark><name>{escape(name)}</name><address>Public street</address>"
        "<description>&lt;b&gt;2&lt;/b&gt; connectors with Type 2 connector"
        "</description><styleUrl>#AVAILABLE</styleUrl>"
        f'<ExtendedData><Data name="CPnum"><value>2</value></Data>{records}'
        f"</ExtendedData><Point><coordinates>{coordinates}</coordinates></Point>"
        "</Placemark>"
    )


def _kml(*places):
    content = "".join(places) if places else _placemark()
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"{content}</Document></kml>"
    ).encode()


def _provider(session):
    provider = create_provider(PROVIDER_CHARGY_LU, session)
    assert isinstance(provider, luxembourg.LuxembourgChargingProvider)
    return provider


async def test_device_mapping_preserves_ids_and_does_not_infer_plug_types():
    devices = [
        _device(connectors=[_connector(), _connector(102, 2, "CHARGING", 400)]),
        _device(2, [_connector(201, 10, "FAULT", None)]),
    ]
    session = FakeSession([FakeResponse(_kml(_placemark(devices)))])
    provider = _provider(session)
    result = await provider.async_get_stations(*LUXEMBOURG, 500, 10)
    assert provider.provider_id == PROVIDER_CHARGY_LU
    assert provider.provider_name == "Chargy Luxembourg"
    assert provider.country_code == COUNTRY_LU
    assert len(result.nearby) == 2
    station, second = result.nearby
    assert station.provider_id == "chargy:1"
    assert station.evse_id == "CP1"
    assert station.name == "Synthetic site - CP1"
    assert station.address == "Public street"
    assert station.operator == "Chargy"
    assert station.country_code == COUNTRY_LU
    assert station.provider_name == provider.provider_name
    assert station.provider == PROVIDER_CHARGY_LU
    assert station.distance_m == 0
    assert station.total_connectors == 2
    assert station.available_connectors == 1
    assert station.occupied_connectors == 1
    assert station.last_updated is None
    assert station.last_updated_source is None
    assert station.provider_data_age_seconds(datetime.now(UTC)) is None
    assert station.tariff is None
    assert station.tariff_status is TariffStatus.NOT_REPORTED
    assert station.connectors[0].provider_connector_id == "101:socket:1"
    assert station.connectors[1].provider_connector_id == "102:socket:2"
    assert station.connectors[1].max_power_kw == 400
    assert second.connectors[0].provider_connector_id == "201:socket:10"
    assert second.connectors[0].max_power_kw is None
    assert second.out_of_service_connectors == 1
    assert all(
        connector.connector_type is None
        and connector.connector_type_id is None
        and connector.tariff_status is TariffStatus.NOT_REPORTED
        for item in result.nearby
        for connector in item.connectors
    )
    assert all(item.distance_m is None for item in provider._snapshot._stations)
    assert session.requests[0][0] == luxembourg._RESOURCE_URL
    assert session.requests[0][1]["User-Agent"] == "CurbCharge"


@pytest.mark.parametrize(
    ("raw", "status", "available"),
    [
        ("AVAILABLE", ConnectorStatus.AVAILABLE, 1),
        ("CHARGING", ConnectorStatus.OCCUPIED, 0),
        ("PREPARING", ConnectorStatus.OCCUPIED, 0),
        ("FINISHING", ConnectorStatus.OCCUPIED, 0),
        ("SUSPENDED_EV", ConnectorStatus.OCCUPIED, 0),
        ("SUSPENDED_EVSE", ConnectorStatus.OCCUPIED, 0),
        ("FAULT", ConnectorStatus.OUT_OF_SERVICE, 0),
        ("OFFLINE", ConnectorStatus.UNKNOWN, None),
        ("UNAVAILABLE", ConnectorStatus.UNKNOWN, None),
        ("FUTURE_STATE", ConnectorStatus.UNKNOWN, None),
    ],
)
async def test_status_vocabulary(raw, status, available):
    body = _kml(_placemark([_device(connectors=[_connector(status=raw)])]))
    station = (
        await _provider(FakeSession([FakeResponse(body)])).async_get_stations(
            *LUXEMBOURG, 500, 10
        )
    ).nearby[0]
    assert station.name == "Synthetic site"
    assert station.connectors[0].status is status
    assert station.available_connectors == available
    assert station.availability_reported is (available is not None)
    assert station.has_available_connectors is (
        None if available is None else bool(available)
    )


async def test_radius_limit_favourites_unknown_and_distance_recalculation():
    body = _kml(
        _placemark([_device(2, [_connector(201, status="OFFLINE")])]),
        _placemark(),
        _placemark([_device(3, [_connector(301)])], "6.14,49.61"),
    )
    session = FakeSession([FakeResponse(body)])
    provider = _provider(session)
    favourite = FavouriteStationReference(
        PROVIDER_CHARGY_LU, "chargy:3", "CP3", 49.61, 6.14
    )
    missing = replace(favourite, provider_id="chargy:missing")
    result = await provider.async_get_stations(
        *LUXEMBOURG, 100, 1, (favourite, missing), include_unknown=False
    )
    assert [s.provider_id for s in result.nearby] == ["chargy:1"]
    assert [s.provider_id for s in result.favourites] == ["chargy:3"]
    assert result.favourites[0].distance_m > 100
    result = await provider.async_get_stations(*LUXEMBOURG, 1000, 3)
    assert [s.provider_id for s in result.nearby] == [
        "chargy:1",
        "chargy:2",
        "chargy:3",
    ]
    unknown_favourite = replace(favourite, provider_id="chargy:2")
    result = await provider.async_get_stations(
        *LUXEMBOURG, 1000, 3, (unknown_favourite,), include_unknown=False
    )
    assert len(result.nearby) == 3
    moved = await provider.async_get_stations(49.61, 6.14, 1000, 3)
    assert moved.nearby[0].provider_id == "chargy:3"
    assert moved.nearby[0].distance_m == 0
    assert len(session.requests) == 1
    with pytest.raises(InvalidProviderResponseError, match="another provider"):
        await provider.async_get_stations(
            *LUXEMBOURG, 1000, 3, (replace(favourite, provider="ndw_dot_nl"),)
        )


async def test_shared_snapshot_validation_concurrency_and_exact_floor(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(luxembourg.time, "monotonic", lambda: now[0])
    session = FakeSession([FakeResponse(_kml()), FakeResponse(_kml())])
    hass = SimpleNamespace(data={})
    first = create_provider(PROVIDER_CHARGY_LU, session, hass)
    second = create_provider(PROVIDER_CHARGY_LU, session, hass)
    assert first._snapshot is second._snapshot
    await asyncio.gather(
        first.async_validate_connection(*LUXEMBOURG, 500),
        second.async_get_stations(*LUXEMBOURG, 500, 10),
    )
    assert len(session.requests) == 1
    now[0] += 299.999
    await second.async_get_stations(*LUXEMBOURG, 500, 10)
    assert len(session.requests) == 1
    now[0] += 0.001
    await second.async_get_stations(*LUXEMBOURG, 500, 10)
    assert len(session.requests) == 2
    hass.data[DOMAIN][luxembourg._SNAPSHOT_DATA_KEY] = "invalid"
    with pytest.raises(RuntimeError, match="invalid type"):
        create_provider(PROVIDER_CHARGY_LU, session, hass)


@pytest.mark.parametrize(
    ("header", "expected"),
    [(None, 300), ("0", 300), ("12", 300), ("700", 700), ("invalid", 300)],
)
async def test_rate_limit_retry_after_and_shared_cooldown(
    monkeypatch, header, expected
):
    now = [1000.0]
    monkeypatch.setattr(luxembourg.time, "monotonic", lambda: now[0])
    session = FakeSession(
        [
            FakeResponse(status=429, headers={"Retry-After": header}),
            FakeResponse(_kml()),
        ]
    )
    hass = SimpleNamespace(data={})
    first = create_provider(PROVIDER_CHARGY_LU, session, hass)
    second = create_provider(PROVIDER_CHARGY_LU, session, hass)
    with pytest.raises(ProviderRateLimitError) as error:
        await first.async_validate_connection(*LUXEMBOURG, 500)
    assert error.value.retry_after == expected
    now[0] += expected - 0.001
    with pytest.raises(ProviderRateLimitError):
        await second.async_get_stations(*LUXEMBOURG, 500, 10)
    assert len(session.requests) == 1
    now[0] += 0.001
    await second.async_get_stations(*LUXEMBOURG, 500, 10)
    assert len(session.requests) == 2


async def test_retry_after_http_date(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(luxembourg.time, "monotonic", lambda: now[0])
    header = format_datetime(datetime.now(UTC) + timedelta(seconds=900), usegmt=True)
    provider = _provider(
        FakeSession([FakeResponse(status=429, headers={"Retry-After": header})])
    )
    with pytest.raises(ProviderRateLimitError) as error:
        await provider.async_get_stations(*LUXEMBOURG, 500, 10)
    assert 899 <= error.value.retry_after <= 900


async def test_repeated_rate_limits_back_off_to_bounded_cap_and_reset(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(luxembourg.time, "monotonic", lambda: now[0])
    session = FakeSession(
        [FakeResponse(status=429) for _ in range(6)]
        + [FakeResponse(_kml()), FakeResponse(status=429)]
    )
    provider = _provider(session)
    for expected in [300, 600, 1200, 2400, 3600, 3600]:
        with pytest.raises(ProviderRateLimitError) as error:
            await provider.async_validate_connection(*LUXEMBOURG, 500)
        assert error.value.retry_after == expected
        now[0] += expected
    await provider.async_validate_connection(*LUXEMBOURG, 500)
    now[0] += 300
    with pytest.raises(ProviderRateLimitError) as error:
        await provider.async_validate_connection(*LUXEMBOURG, 500)
    assert error.value.retry_after == 300


@pytest.mark.parametrize(
    "failure",
    [
        FakeResponse(status=503),
        FakeResponse(status=401),
        FakeResponse(status=429),
        FakeResponse(b"<invalid>"),
        aiohttp.ClientConnectionError("https://publisher.invalid/?key=secret"),
        TimeoutError("https://publisher.invalid/?key=secret"),
    ],
)
async def test_failed_refresh_is_not_stale_success_and_never_leaks_redirect(
    monkeypatch, failure, caplog
):
    now = [1000.0]
    monkeypatch.setattr(luxembourg.time, "monotonic", lambda: now[0])
    session = FakeSession([FakeResponse(_kml()), failure, FakeResponse(_kml())])
    provider = _provider(session)
    await provider.async_get_stations(*LUXEMBOURG, 500, 10)
    now[0] += 300
    for _ in range(2):
        with pytest.raises(
            (
                ProviderConnectionError,
                ProviderRateLimitError,
                InvalidProviderResponseError,
            )
        ) as error:
            await provider.async_get_stations(*LUXEMBOURG, 500, 10)
        assert "secret" not in "".join(traceback.format_exception(error.value))
    assert "secret" not in caplog.text
    assert len(session.requests) == 2
    now[0] += 300
    assert (await provider.async_get_stations(*LUXEMBOURG, 500, 10)).nearby
    assert len(session.requests) == 3


async def test_cancelled_refresh_never_serves_old_snapshot(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(luxembourg.time, "monotonic", lambda: now[0])
    session = FakeSession([FakeResponse(_kml()), asyncio.CancelledError()])
    provider = _provider(session)
    await provider.async_get_stations(*LUXEMBOURG, 500, 10)
    now[0] += 300
    with pytest.raises(asyncio.CancelledError):
        await provider.async_get_stations(*LUXEMBOURG, 500, 10)
    with pytest.raises(ProviderConnectionError, match="did not complete"):
        await provider.async_get_stations(*LUXEMBOURG, 500, 10)


async def test_initial_cancelled_refresh_has_explicit_failure(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(luxembourg.time, "monotonic", lambda: now[0])
    provider = _provider(FakeSession([asyncio.CancelledError()]))
    with pytest.raises(asyncio.CancelledError):
        await provider.async_validate_connection(*LUXEMBOURG, 500)
    with pytest.raises(ProviderConnectionError, match="did not complete"):
        await provider.async_validate_connection(*LUXEMBOURG, 500)


@pytest.mark.parametrize(
    "body",
    [
        b"<broken",
        b"<html>not KML</html>",
        b'<kml xmlns="http://www.opengis.net/kml/2.2"/>',
        _kml(_placemark([])),
        _kml().replace(b'{"id": 1,', b'{"id": true,'),
        _kml().replace(b'{"id": 1,', b'{"id": 0,'),
        _kml().replace(b'{"id": 1,', b'{"id": "1",'),
        _kml().replace(b"CP1", b"   "),
        _kml().replace(b'{"id":', b'{"broken":'),
        _kml(_placemark([_device(connectors=[])])),
        _kml(_placemark([{"id": 1, "name": "CP1", "connectors": "invalid"}])),
        _kml().replace(b'"numberOfConnectors": 1', b'"numberOfConnectors": 2'),
        _kml(_placemark([_device(), _device()])),
        _kml(_placemark([_device(), _device(2)])),
        _kml(_placemark([_device(connectors=[None])])),
        _kml(_placemark([_device(connectors=[_connector(status=None)])])),
        _kml(_placemark([_device(connectors=[_connector(socket=False)])])),
        _kml(_placemark([_device(connectors=[_connector(power="22")])])),
        _kml(_placemark([_device(connectors=[_connector(power=True)])])),
        _kml(_placemark([_device(connectors=[_connector(power=-1)])])),
        _kml(_placemark([_device(connectors=[_connector(power=float("nan"))])])),
        _kml().replace(b"<value>{", b"<value>broken{"),
        _kml(_placemark([None])),
    ],
)
def test_invalid_snapshots_fail_explicitly(body):
    with pytest.raises(InvalidProviderResponseError):
        luxembourg._parse_snapshot(body)


@pytest.mark.parametrize(
    "coordinates", ["", "6", "NaN,49", "6,91", "181,49", "east,north"]
)
def test_invalid_coordinates_are_logged_without_payload(coordinates, caplog):
    bad = _placemark(coordinates=coordinates, name="untrusted private payload")
    stations = luxembourg._parse_snapshot(_kml(bad, _placemark()))
    assert len(stations) == 1
    assert "invalid coordinates" in caplog.text
    assert "untrusted private payload" not in caplog.text
    with pytest.raises(InvalidProviderResponseError, match="no valid devices"):
        luxembourg._parse_snapshot(_kml(bad))


@pytest.mark.parametrize(
    "args",
    [
        (float("nan"), 6, 500, 10),
        (True, 6, 500, 10),
        (91, 6, 500, 10),
        (49, 181, 500, 10),
        (*LUXEMBOURG, 99, 10),
        (*LUXEMBOURG, 10001, 10),
        (*LUXEMBOURG, True, 10),
        (*LUXEMBOURG, 500, 0),
        (*LUXEMBOURG, 500, 51),
        (*LUXEMBOURG, 500, True),
    ],
)
async def test_search_validation_makes_no_request(args):
    session = FakeSession([])
    provider = _provider(session)
    with pytest.raises(UnsupportedLocationError):
        await provider.async_get_stations(*args)
    assert not session.requests


def test_mapping_rejects_non_string_keys():
    with pytest.raises(InvalidProviderResponseError):
        luxembourg._mapping({1: "not JSON"})


async def test_interrupted_initial_state_is_not_success(monkeypatch):
    monkeypatch.setattr(luxembourg.time, "monotonic", lambda: 1000)
    snapshot = luxembourg.LuxembourgSnapshot()
    snapshot._next_allowed_at = 1100
    with pytest.raises(ProviderConnectionError, match="in progress"):
        await snapshot.async_get(FakeSession([]))
