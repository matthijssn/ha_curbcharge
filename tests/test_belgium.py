"""Tests for the Belgium DATEX II HTTP client and request limits."""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from custom_components.curbcharge import belgium
from custom_components.curbcharge.belgium import (
    BelgiumChargingProvider,
    BelgiumDatexClient,
    BelgiumRequestLimiter,
)
from custom_components.curbcharge.models import (
    ConnectorStatus,
    ConnectorTypeId,
    DiscoveryFilterSettings,
    EVSEStatus,
    FavouriteStationReference,
    ProviderTimestampSource,
)
from custom_components.curbcharge.provider import (
    AuthenticationError,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
)


class FakeResponse:
    """Small async response for endpoint contract tests."""

    def __init__(
        self,
        payload: Any,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.body = payload if raw else json.dumps(payload).encode()

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def read(self) -> bytes:
        return self.body


class FakeSession:
    """Record authorized requests and return preloaded responses."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, str]]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: object,
    ) -> FakeResponse:
        self.requests.append((url, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _client(session: FakeSession, token: str = "test-token") -> BelgiumDatexClient:
    return BelgiumDatexClient(session, token, BelgiumRequestLimiter())


class NoWaitLimiter(BelgiumRequestLimiter):
    async def async_wait(self, token: str, evse_id: str | None = None) -> None:
        return None


def _locations_document(
    *,
    number_of_connectors: int | None = 2,
    external_evse_id: str | None = "BE*EXAMPLE*1",
) -> dict[str, Any]:
    document = json.loads(
        (
            Path(__file__).parent / "fixtures" / "belgium_locations_example.json"
        ).read_text(encoding="utf-8")
    )
    point = document["payload"]["aegiEnergyInfrastructureTablePublication"][
        "energyInfrastructureTable"
    ][0]["energyInfrastructureSite"][0]["energyInfrastructureStation"][0][
        "refillPoint"
    ][0]["aegiElectricChargingPoint"]
    point["numberOfConnectors"] = number_of_connectors
    if external_evse_id is not None:
        point["externalIdentifier"] = [
            {
                "identifier": external_evse_id,
                "typeOfIdentifier": {
                    "value": "extendedG",
                    "extendedValueG": "evseId",
                },
            }
        ]
    else:
        point.pop("externalIdentifier", None)
    return document


def _status_document(
    status: str = "available",
    *,
    price: float | None = 0.42,
    currency: str | None = "EUR",
    evse_id: str = "evse-internal-1",
) -> dict[str, Any]:
    document = json.loads(
        (Path(__file__).parent / "fixtures" / "belgium_status_example.json").read_text(
            encoding="utf-8"
        )
    )
    point_status = document["messageContainer"]["payload"][0][
        "aegiEnergyInfrastructureStatusPublication"
    ]["energyInfrastructureSiteStatus"][0]["energyInfrastructureStationStatus"][0][
        "refillPointStatus"
    ][0]["aegiElectricChargingPointStatus"]
    point_status["reference"]["idG"] = evse_id
    point_status["status"]["value"] = status
    if price is not None:
        update = point_status["energyRateUpdate"][0]
        update["energyPrice"][0]["value"] = price
        if currency is not None:
            update["applicableCurrency"] = [currency]
        else:
            update.pop("applicableCurrency", None)
    else:
        point_status.pop("energyRateUpdate", None)
    return document


@pytest.mark.asyncio
async def test_locations_are_authorized_cached_and_parsed_in_executor(monkeypatch):
    parser_threads: list[int] = []
    parse_document = belgium._parse_datex_document

    def record_parser_thread(body: bytes) -> belgium.BelgiumDatexDocument:
        parser_threads.append(threading.get_ident())
        return parse_document(body)

    monkeypatch.setattr(belgium, "_parse_datex_document", record_parser_thread)
    session = FakeSession([FakeResponse({"payload": {"sites": []}})])
    client = _client(session)

    locations = await client.async_get_locations()
    cached = await client.async_get_locations()

    assert cached is locations
    assert locations == {"payload": {"sites": []}}
    assert parser_threads and parser_threads[0] != threading.get_ident()
    assert len(session.requests) == 1
    url, headers = session.requests[0]
    assert url == f"{belgium._API_BASE_URL}/locations"
    assert headers == {"Authorization": "Bearer test-token"}


@pytest.mark.asyncio
async def test_status_request_escapes_evse_id():
    session = FakeSession([FakeResponse({"messageContainer": {}})])
    client = _client(session)

    await client.async_get_evse_status("BE/EVSE 1")

    assert session.requests[0][0] == f"{belgium._API_BASE_URL}/status/BE%2FEVSE%201"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse({}, status=401), AuthenticationError),
        (FakeResponse({}, status=403), AuthenticationError),
        (
            FakeResponse({}, status=429, headers={"Retry-After": "45"}),
            ProviderRateLimitError,
        ),
        (FakeResponse({}, status=503), ProviderConnectionError),
        (TimeoutError("token must not leak"), ProviderConnectionError),
        (FakeResponse(b"{", raw=True), InvalidProviderResponseError),
        (FakeResponse([], raw=False), InvalidProviderResponseError),
    ],
)
async def test_response_errors_are_typed_and_do_not_leak_tokens(response, error):
    token = "super-secret-token"
    client = _client(FakeSession([response]), token)

    with pytest.raises(error) as raised:
        await client.async_get_locations()

    assert token not in str(raised.value)
    if error is ProviderRateLimitError:
        assert raised.value.retry_after == 45


def test_api_token_must_be_printable_non_whitespace_ascii():
    for token in ("", "has space", "non-ascii-\u00e9", "has\nnewline"):
        with pytest.raises(ValueError):
            _client(FakeSession([]), token)


@pytest.mark.asyncio
async def test_belgium_provider_maps_location_evse_connectors_and_status(monkeypatch):
    selection_threads: list[int] = []
    select_static_stations = belgium._select_static_stations

    def record_selection_thread(*args: Any) -> Any:
        selection_threads.append(threading.get_ident())
        return select_static_stations(*args)

    monkeypatch.setattr(belgium, "_select_static_stations", record_selection_thread)
    session = FakeSession(
        [
            FakeResponse(_locations_document()),
            FakeResponse(_status_document()),
        ]
    )
    client = BelgiumDatexClient(session, "token", NoWaitLimiter())
    provider = BelgiumChargingProvider(client)

    stations = (
        await provider.async_get_stations(
            50.85,
            4.35,
            1000,
            10,
            discovery_filters=DiscoveryFilterSettings(
                connector_types=("DOMESTIC_A",),
                minimum_power_kw=500,
                require_available=True,
            ),
            include_unknown=False,
        )
    ).nearby

    assert len(stations) == 1
    station = stations[0]
    assert provider.provider_id == "eco_movement_be"
    assert provider.country_code == "BE"
    assert station.provider_id == "evse-internal-1"
    assert station.evse_id == "BE*EXAMPLE*1"
    assert station.evse_status is EVSEStatus.AVAILABLE
    assert station.last_updated_source is ProviderTimestampSource.BELGIUM_EVSE_STATUS
    assert station.last_updated is not None
    assert station.last_updated.isoformat() == "2026-10-11T10:01:00+00:00"
    assert station.has_available_connectors is True
    assert station.available_connectors is None
    assert station.total_connectors == 2
    assert station.max_power_kw == 50
    assert station.location is not None
    assert station.location.provider_id == "site-1"
    assert station.location.address == "Main Street 1, 1000 Brussels"
    assert station.location.operator == "Example CPO"
    assert station.country_code == "BE"
    assert station.tariff is not None
    assert station.tariff.currency == "EUR"
    assert station.tariff.price_per_kwh == Decimal("0.42")
    assert selection_threads and selection_threads[0] != threading.get_ident()
    assert all(
        connector.status is ConnectorStatus.UNKNOWN
        and connector.available_count is None
        for connector in station.connectors
    )


@pytest.mark.asyncio
async def test_belgium_missing_dynamic_timestamp_uses_static_provenance():
    status_document = _status_document()
    status_record = status_document["messageContainer"]["payload"][0][
        "aegiEnergyInfrastructureStatusPublication"
    ]["energyInfrastructureSiteStatus"][0]["energyInfrastructureStationStatus"][0][
        "refillPointStatus"
    ][0]["aegiElectricChargingPointStatus"]
    status_record.pop("lastUpdated")
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(
            FakeSession(
                [
                    FakeResponse(_locations_document()),
                    FakeResponse(status_document),
                ]
            ),
            "token",
            NoWaitLimiter(),
        )
    )

    station = (await provider.async_get_stations(50.85, 4.35, 1000, 10)).nearby[0]

    assert station.has_available_connectors is True
    assert (
        station.last_updated_source is ProviderTimestampSource.BELGIUM_STATIC_FALLBACK
    )
    assert station.last_updated is not None
    assert station.last_updated.isoformat() == "2026-10-11T10:00:00+00:00"


@pytest.mark.asyncio
async def test_belgium_favourite_outside_radius_uses_cached_location_and_status():
    session = FakeSession(
        [FakeResponse(_locations_document()), FakeResponse(_status_document())]
    )
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(session, "token", NoWaitLimiter())
    )
    favourite = FavouriteStationReference(
        provider=provider.provider_id,
        provider_id="evse-internal-1",
        name="BE*EXAMPLE*1",
        latitude=50.85,
        longitude=4.35,
    )

    result = await provider.async_get_stations(51.0, 4.5, 100, 1, (favourite,))

    assert result.nearby == ()
    assert len(result.favourites) == 1
    assert result.favourites[0].provider_id == favourite.provider_id
    assert result.favourites[0].distance_m > 100
    assert result.favourites[0].evse_status is EVSEStatus.AVAILABLE
    assert len(session.requests) == 2


@pytest.mark.asyncio
async def test_belgium_provider_rejects_favourite_from_another_provider():
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(
            FakeSession([FakeResponse(_locations_document())]),
            "token",
            NoWaitLimiter(),
        )
    )
    favourite = FavouriteStationReference(
        provider="ndw_dot_nl",
        provider_id="station-1",
        name="Station",
        latitude=50.85,
        longitude=4.35,
    )

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_get_stations(50.85, 4.35, 1000, 10, (favourite,))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected", "available"),
    [
        ("charging", EVSEStatus.CHARGING, False),
        ("reserved", EVSEStatus.RESERVED, False),
        ("outOfOrder", EVSEStatus.OUT_OF_ORDER, False),
        ("futureStatus", EVSEStatus.UNKNOWN, None),
    ],
)
async def test_belgium_evse_statuses_preserve_unknown_connector_counts(
    status, expected, available
):
    session = FakeSession(
        [
            FakeResponse(_locations_document(number_of_connectors=3)),
            FakeResponse(_status_document(status, price=None)),
        ]
    )
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(session, "token", NoWaitLimiter())
    )

    station = (await provider.async_get_stations(50.85, 4.35, 1000, 10)).nearby[0]

    assert station.evse_status is expected
    assert station.has_available_connectors is available
    assert station.total_connectors == 3
    assert sum(connector.count for connector in station.connectors) == 3


@pytest.mark.asyncio
async def test_belgium_provider_does_not_use_price_without_currency():
    session = FakeSession(
        [
            FakeResponse(_locations_document()),
            FakeResponse(_status_document(currency=None)),
        ]
    )
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(session, "token", NoWaitLimiter())
    )

    station = (await provider.async_get_stations(50.85, 4.35, 1000, 10)).nearby[0]

    assert station.tariff is None


@pytest.mark.asyncio
async def test_belgium_unmatched_status_keeps_availability_unknown():
    session = FakeSession(
        [
            FakeResponse(_locations_document()),
            FakeResponse(_status_document(evse_id="different-evse", price=None)),
        ]
    )
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(session, "token", NoWaitLimiter())
    )

    station = (await provider.async_get_stations(50.85, 4.35, 1000, 10)).nearby[0]

    assert not station.availability_reported
    assert station.evse_status is None
    assert station.has_available_connectors is None
    assert (
        station.last_updated_source is ProviderTimestampSource.BELGIUM_STATIC_FALLBACK
    )


@pytest.mark.asyncio
async def test_belgium_provider_uses_only_unambiguous_ad_hoc_static_price():
    document = _locations_document()
    evse = document["payload"]["aegiEnergyInfrastructureTablePublication"][
        "energyInfrastructureTable"
    ][0]["energyInfrastructureSite"][0]["energyInfrastructureStation"][0][
        "refillPoint"
    ][0]["aegiElectricChargingPoint"]
    evse["electricEnergy"] = [
        {
            "energyRate": [
                {
                    "ratePolicy": {"value": "adHoc"},
                    "applicableCurrency": ["EUR"],
                    "energyPrice": [
                        {"priceType": {"value": "pricePerKWh"}, "value": 0.37}
                    ],
                },
                {
                    "ratePolicy": {"value": "contract"},
                    "applicableCurrency": ["EUR"],
                    "energyPrice": [
                        {"priceType": {"value": "pricePerKWh"}, "value": 0.2}
                    ],
                },
            ]
        }
    ]
    session = FakeSession(
        [
            FakeResponse(document),
            FakeResponse(_status_document(price=None)),
        ]
    )
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(session, "token", NoWaitLimiter())
    )

    station = (await provider.async_get_stations(50.85, 4.35, 1000, 10)).nearby[0]

    assert station.tariff is not None
    assert station.tariff.currency == "EUR"
    assert station.tariff.price_per_kwh == Decimal("0.37")


@pytest.mark.asyncio
async def test_belgium_removed_evse_is_omitted_after_a_successful_status_poll():
    session = FakeSession(
        [
            FakeResponse(_locations_document()),
            FakeResponse(_status_document("removed", price=None)),
        ]
    )
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(session, "token", NoWaitLimiter())
    )

    assert (await provider.async_get_stations(50.85, 4.35, 1000, 10)).nearby == ()


@pytest.mark.asyncio
async def test_belgium_static_evse_without_external_id_remains_unknown():
    session = FakeSession([FakeResponse(_locations_document(external_evse_id=None))])
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(session, "token", NoWaitLimiter())
    )

    station = (await provider.async_get_stations(50.85, 4.35, 1000, 10)).nearby[0]

    assert station.evse_id is None
    assert station.evse_status is None
    assert not station.availability_reported
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_belgium_provider_filters_by_radius_before_status_requests():
    document = _locations_document()
    table = document["payload"]["aegiEnergyInfrastructureTablePublication"][
        "energyInfrastructureTable"
    ][0]
    first_site = table["energyInfrastructureSite"][0]
    second_site = copy.deepcopy(first_site)
    second_site["idG"] = "site-2"
    second_station = second_site["energyInfrastructureStation"][0]
    second_station["idG"] = "station-2"
    second_station["locationReference"]["locPointLocation"]["coordinatesForDisplay"][
        "latitude"
    ] = 51.0
    second_point = second_station["refillPoint"][0]["aegiElectricChargingPoint"]
    second_point["idG"] = "evse-internal-2"
    second_point["externalIdentifier"][0]["identifier"] = "BE*EXAMPLE*2"
    table["energyInfrastructureSite"].append(second_site)

    session = FakeSession(
        [
            FakeResponse(document),
            FakeResponse(_status_document(price=None)),
        ]
    )
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(session, "token", NoWaitLimiter())
    )

    stations = (await provider.async_get_stations(50.85, 4.35, 1000, 1)).nearby

    assert [station.provider_id for station in stations] == ["evse-internal-1"]
    assert len(session.requests) == 2


@pytest.mark.asyncio
async def test_belgium_provider_recalculates_radius_and_limit_for_moved_center():
    document = _locations_document()
    table = document["payload"]["aegiEnergyInfrastructureTablePublication"][
        "energyInfrastructureTable"
    ][0]
    first_site = table["energyInfrastructureSite"][0]
    second_site = copy.deepcopy(first_site)
    second_site["idG"] = "site-2"
    second_station = second_site["energyInfrastructureStation"][0]
    second_station["idG"] = "station-2"
    second_station["locationReference"]["locPointLocation"]["coordinatesForDisplay"][
        "latitude"
    ] = 51.0
    second_point = second_station["refillPoint"][0]["aegiElectricChargingPoint"]
    second_point["idG"] = "evse-internal-2"
    second_point["externalIdentifier"][0]["identifier"] = "BE*EXAMPLE*2"
    table["energyInfrastructureSite"].append(second_site)

    for radius_m, max_stations in ((20_000, 1), (1_000, 10)):
        session = FakeSession(
            [
                FakeResponse(copy.deepcopy(document)),
                FakeResponse(_status_document()),
            ]
        )
        provider = BelgiumChargingProvider(
            BelgiumDatexClient(session, "token", NoWaitLimiter())
        )

        stations = (
            await provider.async_get_stations(51.0, 4.35, radius_m, max_stations)
        ).nearby

        assert [station.provider_id for station in stations] == ["evse-internal-2"]
        assert stations[0].distance_m == 0
        assert len(session.requests) == 2


@pytest.mark.asyncio
async def test_request_limiter_enforces_ip_location_and_per_evse_limits(monkeypatch):
    current_time = [0.0]
    waits: list[float] = []

    def monotonic() -> float:
        return current_time[0]

    async def sleep(delay: float) -> None:
        waits.append(delay)
        current_time[0] += delay

    monkeypatch.setattr(belgium.time, "monotonic", monotonic)
    monkeypatch.setattr(belgium.asyncio, "sleep", sleep)
    limiter = BelgiumRequestLimiter()

    await limiter.async_wait("token", "evse-1")
    await limiter.async_wait("token", "evse-1")
    assert current_time[0] == 30

    await limiter.async_wait("token", "evse-2")
    assert current_time[0] == 40

    await limiter.async_wait("token")
    await limiter.async_wait("token")
    assert current_time[0] == 170
    assert waits == [30, 10, 10, 120]


@pytest.mark.asyncio
async def test_retry_after_updates_a_request_already_waiting(monkeypatch):
    current_time = [0.0]
    waits: list[float] = []
    waiting_for_sleep = asyncio.Event()
    continue_sleep = asyncio.Event()

    def monotonic() -> float:
        return current_time[0]

    async def sleep(delay: float) -> None:
        waits.append(delay)
        if len(waits) == 1:
            waiting_for_sleep.set()
            await continue_sleep.wait()
        current_time[0] += delay

    monkeypatch.setattr(belgium.time, "monotonic", monotonic)
    monkeypatch.setattr(belgium.asyncio, "sleep", sleep)
    limiter = BelgiumRequestLimiter()

    await limiter.async_wait("first-token")
    waiting_request = asyncio.create_task(limiter.async_wait("second-token"))
    await waiting_for_sleep.wait()
    limiter.defer_requests(45)
    continue_sleep.set()
    await waiting_request

    assert current_time[0] == 45
    assert waits == [10, 35]


@pytest.mark.asyncio
async def test_belgium_provider_factory_shares_rate_limits_between_entries(
    hass, monkeypatch
):
    current_time = [0.0]
    waits: list[float] = []

    def monotonic() -> float:
        return current_time[0]

    async def sleep(delay: float) -> None:
        waits.append(delay)
        current_time[0] += delay

    monkeypatch.setattr(belgium.time, "monotonic", monotonic)
    monkeypatch.setattr(belgium.asyncio, "sleep", sleep)
    providers = [
        belgium.create_belgium_provider(
            hass,
            FakeSession([FakeResponse(_locations_document())]),
            token,
        )
        for token in ("shared-token", "shared-token", "other-token")
    ]

    for provider in providers:
        await provider.async_validate_connection(50.85, 4.35, 1000)

    assert waits == [120, 10]


@pytest.mark.asyncio
async def test_retry_after_backoff_is_shared_between_provider_instances(
    hass, monkeypatch
):
    current_time = [0.0]
    waits: list[float] = []

    def monotonic() -> float:
        return current_time[0]

    async def sleep(delay: float) -> None:
        waits.append(delay)
        current_time[0] += delay

    monkeypatch.setattr(belgium.time, "monotonic", monotonic)
    monkeypatch.setattr(belgium.asyncio, "sleep", sleep)
    rate_limited_provider = belgium.create_belgium_provider(
        hass,
        FakeSession([FakeResponse({}, status=429, headers={"Retry-After": "45"})]),
        "first-token",
    )
    other_provider = belgium.create_belgium_provider(
        hass,
        FakeSession([FakeResponse(_locations_document())]),
        "second-token",
    )

    with pytest.raises(ProviderRateLimitError) as raised:
        await rate_limited_provider.async_validate_connection(50.85, 4.35, 1000)
    assert raised.value.retry_after == 45

    await other_provider.async_validate_connection(50.85, 4.35, 1000)

    assert current_time[0] == 45
    assert waits == [45]


def test_shared_belgium_limiter_rejects_invalid_hass_data(hass):
    domain_data = hass.data.setdefault(belgium.DOMAIN, {})
    domain_data["belgium_request_limiter"] = object()

    with pytest.raises(RuntimeError, match="invalid type"):
        belgium._shared_request_limiter(hass)


@pytest.mark.asyncio
async def test_belgium_provider_validates_the_static_location_snapshot():
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(
            FakeSession([FakeResponse(_locations_document())]),
            "token",
            NoWaitLimiter(),
        )
    )

    await provider.async_validate_connection(50.85, 4.35, 1000)


@pytest.mark.asyncio
async def test_belgium_provider_rejects_unsupported_radius_before_network_call():
    session = FakeSession([])
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(session, "token", NoWaitLimiter())
    )

    with pytest.raises(UnsupportedLocationError):
        await provider.async_validate_connection(50.85, 4.35, 0)

    assert session.requests == []


@pytest.mark.asyncio
async def test_belgium_provider_rejects_a_location_without_coordinates():
    document = _locations_document()
    site = document["payload"]["aegiEnergyInfrastructureTablePublication"][
        "energyInfrastructureTable"
    ][0]["energyInfrastructureSite"][0]
    site["locationReference"] = {}
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(
            FakeSession([FakeResponse(document)]), "token", NoWaitLimiter()
        )
    )

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_validate_connection(50.85, 4.35, 1000)


def test_belgium_datex_helpers_handle_optional_address_fields_and_codes():
    for site in (
        {"locationReference": {}},
        {"locationReference": {"locAreaLocation": {}}},
        {"locationReference": {"locAreaLocation": {"locLocationExtensionG": {}}}},
        {
            "locationReference": {
                "locAreaLocation": {"locLocationExtensionG": {"FacilityLocation": {}}}
            }
        },
    ):
        assert belgium._site_address(site) == ("BE", None)

    with pytest.raises(InvalidProviderResponseError):
        belgium._site_address(
            {
                "locationReference": {
                    "locAreaLocation": {
                        "locLocationExtensionG": {
                            "FacilityLocation": {"address": {"countryCode": "DE"}}
                        }
                    }
                }
            }
        )

    assert belgium._operator_name(None) is None
    assert belgium._operator_name({"name": {"value": "Example operator"}}) == (
        "Example operator"
    )
    assert belgium._operator_name({"afacAnOrganisation": {"name": None}}) is None
    assert (
        belgium._external_evse_id(
            {"externalIdentifier": [{"identifier": "unknown", "typeOfIdentifier": []}]}
        )
        is None
    )


def test_belgium_location_parser_skips_unreadable_address_lines():
    document = _locations_document()
    site = document["payload"]["aegiEnergyInfrastructureTablePublication"][
        "energyInfrastructureTable"
    ][0]["energyInfrastructureSite"][0]
    address = site["locationReference"]["locAreaLocation"]["locLocationExtensionG"][
        "FacilityLocation"
    ]["address"]
    address.pop("countryCode")
    address["addressLine"].append({"order": 2, "type": {"value": "street"}})

    station = belgium._parse_static_stations(document, 50.85, 4.35)[0]

    assert station.location is not None
    assert station.location.address == "Main Street 1, 1000 Brussels"
    assert station.country_code == "BE"


def test_belgium_connector_groups_validate_declared_counts_and_power():
    connector = {"connectorType": {"value": "iec62196T2"}}
    chademo = {"connectorType": {"value": "chademo"}}
    inferred_connectors, inferred_count = belgium._connector_groups(
        "evse", {"connector": [connector, chademo]}
    )
    assert inferred_count
    assert {
        item.connector_type: item.connector_type_id for item in inferred_connectors
    } == {
        "iec62196T2": ConnectorTypeId.TYPE_2,
        "chademo": ConnectorTypeId.CHADEMO,
    }
    assert all(item.count == 1 for item in inferred_connectors)

    connectors, reported = belgium._connector_groups(
        "evse",
        {
            "connector": [
                {
                    "connectorType": {"value": "iec62196T2"},
                    "maxPowerAtSocket": 22000,
                }
            ]
        },
    )
    assert reported
    assert connectors[0].max_power_kw == 22
    assert connectors[0].connector_type_id is ConnectorTypeId.TYPE_2

    unknown, _ = belgium._connector_groups(
        "evse", {"connector": [{"connectorType": {"value": "futureConnector"}}]}
    )
    assert unknown[0].connector_type == "futureConnector"
    assert unknown[0].connector_type_id is None

    invalid_records = (
        {"numberOfConnectors": True},
        {"numberOfConnectors": -1},
        {
            "numberOfConnectors": 0,
            "connector": [{"connectorType": {"value": "t2"}}],
        },
        {
            "numberOfConnectors": 1,
            "connector": [
                {
                    "connectorType": {"value": "t2"},
                    "maxPowerAtSocket": -1,
                }
            ],
        },
    )
    for record in invalid_records:
        with pytest.raises(InvalidProviderResponseError):
            belgium._connector_groups("evse", record)


def test_belgium_evse_status_parser_handles_missing_and_unmatched_records():
    documents = (
        {"messageContainer": {}},
        {"messageContainer": {"payload": [{}]}},
        {
            "messageContainer": {
                "payload": [
                    {
                        "aegiEnergyInfrastructureStatusPublication": {
                            "energyInfrastructureSiteStatus": []
                        }
                    }
                ]
            }
        },
        {
            "messageContainer": {
                "payload": [
                    {
                        "aegiEnergyInfrastructureStatusPublication": {
                            "energyInfrastructureSiteStatus": [
                                {
                                    "energyInfrastructureStationStatus": [
                                        {
                                            "refillPointStatus": [
                                                {
                                                    "aegiElectricChargingPointStatus": {
                                                        "reference": None
                                                    }
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
        },
        {
            "messageContainer": {
                "payload": [
                    {
                        "aegiEnergyInfrastructureStatusPublication": {
                            "energyInfrastructureSiteStatus": [
                                {
                                    "energyInfrastructureStationStatus": [
                                        {"refillPointStatus": [{}]}
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
        },
        {
            "messageContainer": {
                "payload": [
                    {
                        "aegiEnergyInfrastructureStatusPublication": {
                            "energyInfrastructureSiteStatus": [
                                {
                                    "energyInfrastructureStationStatus": [
                                        {
                                            "refillPointStatus": [
                                                {
                                                    "aegiElectricChargingPointStatus": {
                                                        "reference": {"idG": "other"}
                                                    }
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ]
            }
        },
    )
    for document in documents:
        assert belgium._evse_status_record(document, "wanted") is None

    with pytest.raises(InvalidProviderResponseError):
        belgium._evse_status_record({}, "wanted")


@pytest.mark.asyncio
async def test_belgium_price_and_optional_field_helpers_preserve_unknowns():
    assert belgium._ad_hoc_tariff({}, "Belgium") is None
    assert belgium._ad_hoc_tariff({"energyRateUpdate": []}, "Belgium") is None
    assert (
        belgium._ad_hoc_tariff(
            {
                "energyRateUpdate": [
                    {
                        "applicableCurrency": ["EURO"],
                        "energyPrice": [{"priceType": "pricePerKWh", "value": 0.3}],
                    },
                    {"applicableCurrency": ["EUR"]},
                    {
                        "applicableCurrency": ["EUR"],
                        "energyPrice": [{"priceType": "pricePerHour", "value": 2}],
                    },
                    {
                        "applicableCurrency": ["EUR"],
                        "energyPrice": [
                            {
                                "priceType": "pricePerKWh",
                                "value": 0.3,
                                "overallPeriod": {},
                            }
                        ],
                    },
                ]
            },
            "Belgium",
        )
        is None
    )
    assert belgium._static_ad_hoc_tariff({}, "Belgium") is None
    assert belgium._static_ad_hoc_tariff({"electricEnergy": [{}]}, "Belgium") is None
    assert (
        belgium._static_ad_hoc_tariff(
            {
                "electricEnergy": [
                    {"energyRate": [{"ratePolicy": {"value": "contract"}}]}
                ]
            },
            "Belgium",
        )
        is None
    )
    assert (
        belgium._static_ad_hoc_tariff(
            {
                "electricEnergy": [
                    {
                        "energyRate": [
                            {
                                "ratePolicy": "adHoc",
                                "applicableCurrency": ["EUR", "USD"],
                                "energyPrice": [
                                    {"priceType": "pricePerKWh", "value": 1}
                                ],
                            },
                            {
                                "ratePolicy": "adHoc",
                                "applicableCurrency": ["EU"],
                                "energyPrice": [
                                    {"priceType": "pricePerKWh", "value": 1}
                                ],
                            },
                            {
                                "ratePolicy": "adHoc",
                                "applicableCurrency": ["EUR"],
                            },
                            {
                                "ratePolicy": "adHoc",
                                "applicableCurrency": ["EUR"],
                                "energyPrice": [
                                    {"priceType": "pricePerHour", "value": 2}
                                ],
                            },
                            {
                                "ratePolicy": "adHoc",
                                "applicableCurrency": ["EUR"],
                                "energyPrice": [
                                    {
                                        "priceType": "pricePerKWh",
                                        "value": 0.3,
                                        "overallPeriod": {},
                                    }
                                ],
                            },
                        ]
                    }
                ]
            },
            "Belgium",
        )
        is None
    )
    assert belgium._optional_date(None, "EVSE") is None
    assert belgium._optional_datex_string(None, "identifier") is None
    assert belgium._location_coordinates(None, "site") is None
    assert belgium._location_coordinates({"unknown": {}}, "site") is None
    assert (
        belgium._location_coordinates(
            {"locAreaLocation": {"coordinatesForDisplay": None}}, "site"
        )
        is None
    )
    with pytest.raises(InvalidProviderResponseError):
        belgium._location_coordinates(
            {
                "locAreaLocation": {
                    "coordinatesForDisplay": {"latitude": 91, "longitude": 0}
                }
            },
            "site",
        )
    assert belgium._localized_text("Name") == "Name"
    assert belgium._localized_text(None) is None
    assert belgium._localized_text({"values": "invalid"}) is None
    assert belgium._localized_text({"values": [{}, {"value": None}]}) is None
    assert belgium._datex_code("available") == "available"
    assert belgium._datex_code({"value": "available"}) == "available"
    assert belgium._datex_code(None) is None

    invalid_calls = (
        lambda: belgium._datex_object([], "object"),
        lambda: belgium._datex_object({1: "value"}, "object"),
        lambda: belgium._datex_list({}, "list"),
        lambda: belgium._required_datex_string("", "identifier"),
        lambda: belgium._datex_number(True, "number"),
        lambda: belgium._datex_number(float("nan"), "number"),
        lambda: belgium._datex_decimal(object(), "price"),
        lambda: belgium._datex_decimal("not-a-price", "price"),
        lambda: belgium._datex_decimal(-1, "price"),
        lambda: belgium._optional_date(1, "EVSE"),
        lambda: belgium._optional_date("not-a-date", "EVSE"),
        lambda: belgium._optional_date("2026-10-11T12:00:00", "EVSE"),
    )
    for call in invalid_calls:
        with pytest.raises(InvalidProviderResponseError):
            call()

    with pytest.raises(InvalidProviderResponseError):
        await _client(FakeSession([])).async_get_evse_status("")
