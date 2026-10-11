"""Tests for the Belgium DATEX II HTTP client and request limits."""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from typing import Any

import pytest

from custom_components.curbcharge import belgium
from custom_components.curbcharge.belgium import (
    BelgiumChargingProvider,
    BelgiumDatexClient,
    BelgiumRequestLimiter,
)
from custom_components.curbcharge.models import ConnectorStatus, EVSEStatus
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
    point: dict[str, Any] = {
        "idG": "evse-internal-1",
        "lastUpdated": "2026-10-11T12:00:00+02:00",
        "numberOfConnectors": number_of_connectors,
        "connector": [
            {
                "connectorType": {"value": "iec62196T2"},
                "maxPowerAtSocket": 22000,
            },
            {"connectorType": {"value": "chademo"}, "maxPowerAtSocket": 50000},
        ],
    }
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
    site = {
        "idG": "site-1",
        "lastUpdated": "2026-10-11T11:00:00+02:00",
        "locationReference": {
            "locAreaLocation": {
                "coordinatesForDisplay": {"latitude": 50.85, "longitude": 4.35},
                "locLocationExtensionG": {
                    "FacilityLocation": {
                        "address": {
                            "countryCode": "BE",
                            "postcode": "1000",
                            "city": {"values": [{"lang": "en", "value": "Brussels"}]},
                            "addressLine": [
                                {
                                    "order": 0,
                                    "type": {"value": "street"},
                                    "text": {
                                        "values": [
                                            {"lang": "en", "value": "Main Street"}
                                        ]
                                    },
                                },
                                {
                                    "order": 1,
                                    "type": {"value": "houseNumber"},
                                    "text": {"values": [{"lang": "en", "value": "1"}]},
                                },
                            ],
                        }
                    }
                },
            }
        },
        "operator": {
            "afacAnOrganisation": {
                "name": {"values": [{"lang": "en", "value": "Example CPO"}]}
            }
        },
        "energyInfrastructureStation": [
            {
                "idG": "station-1",
                "locationReference": {
                    "locPointLocation": {
                        "coordinatesForDisplay": {
                            "latitude": 50.85,
                            "longitude": 4.35,
                        }
                    }
                },
                "refillPoint": [{"aegiElectricChargingPoint": point}],
            }
        ],
    }
    return {
        "payload": {
            "aegiEnergyInfrastructureTablePublication": {
                "energyInfrastructureTable": [{"energyInfrastructureSite": [site]}]
            }
        }
    }


def _status_document(
    status: str = "available",
    *,
    price: float | None = 0.42,
    currency: str | None = "EUR",
    evse_id: str = "evse-internal-1",
) -> dict[str, Any]:
    point_status: dict[str, Any] = {
        "reference": {"idG": evse_id},
        "lastUpdated": "2026-10-11T12:01:00+02:00",
        "status": {"value": status},
    }
    if price is not None:
        update: dict[str, Any] = {
            "energyPrice": [{"priceType": {"value": "pricePerKWh"}, "value": price}]
        }
        if currency is not None:
            update["applicableCurrency"] = [currency]
        point_status["energyRateUpdate"] = [update]
    return {
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
                                                "aegiElectricChargingPointStatus": (
                                                    point_status
                                                )
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
    }


@pytest.mark.asyncio
async def test_locations_are_authorized_and_cached():
    session = FakeSession([FakeResponse({"payload": {"sites": []}})])
    client = _client(session)

    locations = await client.async_get_locations()
    locations["payload"]["modified"] = True
    cached = await client.async_get_locations()

    assert cached == {"payload": {"sites": []}}
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
async def test_belgium_provider_maps_location_evse_connectors_and_status():
    session = FakeSession(
        [
            FakeResponse(_locations_document()),
            FakeResponse(_status_document()),
        ]
    )
    client = BelgiumDatexClient(session, "token", NoWaitLimiter())
    provider = BelgiumChargingProvider(client)

    stations = await provider.async_get_stations(50.85, 4.35, 1000, 10)

    assert len(stations) == 1
    station = stations[0]
    assert provider.provider_id == "eco_movement_be"
    assert provider.country_code == "BE"
    assert station.provider_id == "evse-internal-1"
    assert station.evse_id == "BE*EXAMPLE*1"
    assert station.evse_status is EVSEStatus.AVAILABLE
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
    assert all(
        connector.status is ConnectorStatus.UNKNOWN
        and connector.available_count is None
        for connector in station.connectors
    )


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

    station = (await provider.async_get_stations(50.85, 4.35, 1000, 10))[0]

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

    station = (await provider.async_get_stations(50.85, 4.35, 1000, 10))[0]

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

    station = (await provider.async_get_stations(50.85, 4.35, 1000, 10))[0]

    assert not station.availability_reported
    assert station.evse_status is None
    assert station.has_available_connectors is None


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

    station = (await provider.async_get_stations(50.85, 4.35, 1000, 10))[0]

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

    assert await provider.async_get_stations(50.85, 4.35, 1000, 10) == ()


@pytest.mark.asyncio
async def test_belgium_static_evse_without_external_id_remains_unknown():
    session = FakeSession([FakeResponse(_locations_document(external_evse_id=None))])
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(session, "token", NoWaitLimiter())
    )

    station = (await provider.async_get_stations(50.85, 4.35, 1000, 10))[0]

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

    stations = await provider.async_get_stations(50.85, 4.35, 1000, 1)

    assert [station.provider_id for station in stations] == ["evse-internal-1"]
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
    inferred_connectors, inferred_count = belgium._connector_groups(
        "evse", {"connector": [connector]}
    )
    assert inferred_count
    assert inferred_connectors[0].count == 1

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
    assert belgium._optional_date(None, "EVSE") is None
    assert belgium._location_coordinates(None, "site") is None
    assert belgium._location_coordinates({"unknown": {}}, "site") is None
    assert belgium._localized_text("Name") == "Name"
    assert belgium._localized_text(None) is None
    assert belgium._localized_text({"values": "invalid"}) is None
    assert belgium._datex_code("available") == "available"
    assert belgium._datex_code({"value": "available"}) == "available"
    assert belgium._datex_code(None) is None

    invalid_calls = (
        lambda: belgium._datex_object([], "object"),
        lambda: belgium._datex_object({1: "value"}, "object"),
        lambda: belgium._datex_list({}, "list"),
        lambda: belgium._required_datex_string("", "identifier"),
        lambda: belgium._datex_number(True, "number"),
        lambda: belgium._datex_decimal(object(), "price"),
        lambda: belgium._datex_decimal("not-a-price", "price"),
        lambda: belgium._datex_decimal(-1, "price"),
        lambda: belgium._optional_date("not-a-date", "EVSE"),
        lambda: belgium._optional_date("2026-10-11T12:00:00", "EVSE"),
    )
    for call in invalid_calls:
        with pytest.raises(InvalidProviderResponseError):
            call()

    with pytest.raises(InvalidProviderResponseError):
        await _client(FakeSession([])).async_get_evse_status("")
