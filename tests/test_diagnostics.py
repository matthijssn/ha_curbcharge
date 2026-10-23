"""Tests for diagnostics privacy."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.curbcharge.belgium import (
    BelgiumChargingProvider,
    BelgiumDatexClient,
)
from custom_components.curbcharge.const import (
    CONF_API_TOKEN,
    CONF_FAVOURITES,
    CONF_LOCATION_ENTITY,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SEARCH_RADIUS,
    DOMAIN,
    LOCATION_ENTITY,
    LOCATION_MANUAL,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_NDW_DOT_NL,
)
from custom_components.curbcharge.coordinator import CurbChargeCoordinator
from custom_components.curbcharge.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.curbcharge.models import (
    ChargingTariff,
    EVSEStatus,
    FavouriteStationReference,
    ProviderTimestampSource,
)
from custom_components.curbcharge.provider import ProviderConnectionError
from tests.conftest import FakeProvider
from tests.test_belgium import (
    FakeResponse,
    FakeSession,
    NoWaitLimiter,
    _locations_document,
    _status_document,
)


@pytest.mark.asyncio
async def test_diagnostics_redact_coordinates_and_addresses(
    hass, sample_stations, monkeypatch
):
    now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    station = replace(
        sample_stations[0],
        tariff=ChargingTariff("EUR", Decimal("0.42"), None, None, None, "NDW DOT-NL"),
        last_updated=now - timedelta(seconds=65),
        last_updated_source=ProviderTimestampSource.NDW_FEATURE,
    )
    stale = replace(sample_stations[1], is_stale=True)
    unknown = sample_stations[2]
    provider = FakeProvider([(station, stale, unknown)])
    coordinator = CurbChargeCoordinator(hass, provider, 52.0, 5.0, 500, 10, 5, True)
    coordinator.async_set_updated_data(
        {
            station.provider_id: station,
            stale.provider_id: stale,
            unknown.provider_id: unknown,
        }
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="hash-only",
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_LATITUDE: 52.123456,
            CONF_LONGITUDE: 5.654321,
            CONF_API_TOKEN: "diagnostic-secret-token",
            "provider_credentials": {
                CONF_API_TOKEN: "nested-data-secret-token",
            },
        },
        options={
            CONF_API_TOKEN: "options-secret-token",
            "provider_credentials": {
                CONF_API_TOKEN: "nested-options-secret-token",
            },
        },
    )
    entry.runtime_data = coordinator
    monkeypatch.setattr(
        "custom_components.curbcharge.diagnostics.dt_util.utcnow", lambda: now
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics)

    assert "52.123456" not in serialized
    assert "5.654321" not in serialized
    assert "Teststraat 1" not in serialized
    assert "google.com/maps/dir" not in serialized
    assert "maps.apple.com/directions" not in serialized
    assert "waze.com/ul?" not in serialized
    assert "geo:" not in serialized
    assert stale.provider_id not in serialized
    assert "diagnostic-secret-token" not in serialized
    assert "nested-data-secret-token" not in serialized
    assert "options-secret-token" not in serialized
    assert "nested-options-secret-token" not in serialized
    assert diagnostics["provider"] == "NDW DOT-NL"
    assert diagnostics["provider_id"] == "ndw_dot_nl"
    assert diagnostics["country_code"] == "NL"
    assert diagnostics["discovered_stations"] == 2
    assert diagnostics["retained_stations"] == 1
    assert len(diagnostics["stations"]) == 2
    assert diagnostics["stations"][0]["available_connectors"] == 2
    assert diagnostics["stations"][0]["distance_m_rounded"] == 0
    assert diagnostics["stations"][0]["provider_data_age_seconds"] == 65
    assert diagnostics["stations"][0]["provider_timestamp_source"] == "ndw_feature"
    assert diagnostics["stations"][0]["tariff"]["price_per_kwh"] == "0.42"
    unknown_diagnostics = next(
        station
        for station in diagnostics["stations"]
        if station["provider_station_id"] == unknown.provider_id
    )
    assert unknown_diagnostics["availability_reported"] is False
    assert unknown_diagnostics["available_connectors"] is None
    assert unknown_diagnostics[
        "provider_data_age_seconds"
    ] == unknown.provider_data_age_seconds(now)
    assert unknown_diagnostics["provider_timestamp_source"] == "ndw_feature"
    assert len(provider.snapshots) == 1


@pytest.mark.asyncio
async def test_diagnostics_redact_entity_location_id_and_exact_coordinates(hass):
    entity_id = "device_tracker.private_vehicle"
    latitude = 52.123456
    longitude = 5.654321
    hass.states.async_set(
        entity_id,
        "moving",
        {CONF_LATITUDE: latitude, CONF_LONGITUDE: longitude},
    )
    coordinator = CurbChargeCoordinator(
        hass, FakeProvider(), latitude, longitude, 500, 10, 5, True
    )
    coordinator.async_set_updated_data({})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="entity-search-area",
        data={
            CONF_LOCATION_SOURCE: LOCATION_ENTITY,
            CONF_LOCATION_ENTITY: entity_id,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
        },
    )
    entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics)

    assert entity_id not in serialized
    assert str(latitude) not in serialized
    assert str(longitude) not in serialized
    assert CONF_LATITUDE not in diagnostics["entry_data"]
    assert CONF_LONGITUDE not in diagnostics["entry_data"]


@pytest.mark.asyncio
async def test_diagnostics_redact_favourite_references_and_station_details(
    hass, sample_stations
):
    station = replace(
        sample_stations[0],
        provider_id="private-favourite-id",
        name="Private favourite name",
        address="Private favourite address",
        latitude=52.123456,
        longitude=5.654321,
    )
    favourite = FavouriteStationReference(
        provider=station.provider,
        provider_id=station.provider_id,
        name=station.name,
        latitude=station.latitude,
        longitude=station.longitude,
    )
    provider = FakeProvider([(station,)])
    coordinator = CurbChargeCoordinator(
        hass,
        provider,
        52.0,
        5.0,
        500,
        10,
        5,
        True,
        (favourite,),
    )
    coordinator.async_set_updated_data({station.provider_id: station})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="favorite-area",
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
        },
        options={CONF_FAVOURITES: [favourite.as_config()]},
    )
    entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics)

    assert "private-favourite-id" not in serialized
    assert "Private favourite name" not in serialized
    assert "Private favourite address" not in serialized
    assert "52.123456" not in serialized
    assert "5.654321" not in serialized
    assert diagnostics["discovered_stations"] == 1
    assert diagnostics["favourite_count"] == 1
    assert diagnostics["stations"] == []
    assert diagnostics["coordinator_update_success"]
    assert diagnostics["favourite_references"] == [
        {
            "provider": favourite.provider,
            "station_id_hash": hashlib.sha256(
                f"{entry.entry_id}:{favourite.provider}:{favourite.provider_id}".encode()
            ).hexdigest()[:16],
            "presence": "present",
            "missing_refresh_count": 0,
        }
    ]


@pytest.mark.asyncio
async def test_favourite_diagnostics_distinguish_stale_missing_and_refresh_failure(
    hass, sample_stations
):
    favourite = sample_stations[0]
    provider = FakeProvider(
        [
            (favourite,),
            (),
            (),
            (),
            ProviderConnectionError("offline"),
        ]
    )
    coordinator = CurbChargeCoordinator(
        hass,
        provider,
        52.0,
        5.0,
        500,
        10,
        5,
        True,
        (
            FavouriteStationReference(
                provider=favourite.provider,
                provider_id=favourite.provider_id,
                name=favourite.provider_id,
                latitude=favourite.latitude,
                longitude=favourite.longitude,
            ),
        ),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="favorite-freshness",
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        },
    )
    entry.runtime_data = coordinator

    await coordinator.async_refresh()
    present = await async_get_config_entry_diagnostics(hass, entry)
    assert present["favourite_references"][0]["presence"] == "present"

    coordinator.async_set_updated_data({})
    not_discovered = await async_get_config_entry_diagnostics(hass, entry)
    assert not_discovered["favourite_references"][0]["presence"] == "not_discovered"

    await coordinator.async_refresh()
    stale = await async_get_config_entry_diagnostics(hass, entry)
    assert stale["favourite_references"][0]["presence"] == "stale"
    assert stale["favourite_references"][0]["missing_refresh_count"] == 1

    await coordinator.async_refresh()
    await coordinator.async_refresh()
    missing = await async_get_config_entry_diagnostics(hass, entry)
    assert missing["favourite_references"][0]["presence"] == "missing"
    assert missing["favourite_references"][0]["missing_refresh_count"] == 3

    await coordinator.async_refresh()
    failed = await async_get_config_entry_diagnostics(hass, entry)
    assert not failed["coordinator_update_success"]
    assert failed["favourite_references"][0]["presence"] == "refresh_failed"
    assert failed["favourite_references"][0]["missing_refresh_count"] == 3


@pytest.mark.asyncio
async def test_belgium_diagnostics_redact_nested_secrets_and_location(hass):
    token = "belgium-secret-token"
    session = FakeSession(
        [
            FakeResponse(_locations_document()),
            FakeResponse(_status_document()),
        ]
    )
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(session, token, NoWaitLimiter())
    )
    stations = (await provider.async_get_stations(50.85, 4.35, 1000, 10)).nearby
    assert len(stations) == 1
    station = stations[0]
    stale = replace(
        station,
        provider_id="evse-stale",
        evse_id="BE*STALE*EVSE",
        is_stale=True,
    )
    unknown = replace(
        station,
        provider_id="evse-unknown",
        evse_id="BE*UNKNOWN*EVSE",
        evse_status=EVSEStatus.UNKNOWN,
        availability_reported=False,
        connector_count_reported=False,
    )
    provider = FakeProvider([(station, stale, unknown)])
    provider.provider_id = PROVIDER_ECO_MOVEMENT_BE
    provider.provider_name = "Eco-Movement Belgium"
    provider.country_code = "BE"
    coordinator = CurbChargeCoordinator(hass, provider, 50.85, 4.35, 500, 10, 5, True)
    coordinator.async_set_updated_data(
        {
            station.provider_id: station,
            stale.provider_id: stale,
            unknown.provider_id: unknown,
        }
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="hashed-belgium-area",
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_LATITUDE: 50.999999,
            CONF_LONGITUDE: 4.999999,
            CONF_API_TOKEN: token,
            "provider_credentials": {
                CONF_API_TOKEN: "nested-belgium-secret-token",
            },
        },
        options={
            CONF_API_TOKEN: "belgium-options-secret-token",
            "provider_credentials": {
                CONF_API_TOKEN: "nested-belgium-options-secret-token",
            },
        },
    )
    entry.runtime_data = coordinator

    request_count = len(session.requests)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics)

    for private_value in (
        "belgium-secret-token",
        "nested-belgium-secret-token",
        "belgium-options-secret-token",
        "nested-belgium-options-secret-token",
        "evse-stale",
        "50.999999",
        "4.999999",
        "50.85",
        "4.35",
        "Main Street 1, 1000 Brussels",
        "aegiEnergyInfrastructureTablePublication",
    ):
        assert private_value not in serialized
    for url, _headers in session.requests:
        assert url not in serialized

    assert diagnostics["provider"] == "Eco-Movement Belgium"
    assert diagnostics["provider_id"] == PROVIDER_ECO_MOVEMENT_BE
    assert diagnostics["country_code"] == "BE"
    assert diagnostics["discovered_stations"] == 2
    assert diagnostics["retained_stations"] == 1
    assert len(diagnostics["stations"]) == 2
    evse_diagnostics = next(
        station
        for station in diagnostics["stations"]
        if station["provider_station_id"] == "evse-internal-1"
    )
    assert evse_diagnostics["evse_status"] == "AVAILABLE"
    assert evse_diagnostics["available_connectors"] is None
    assert evse_diagnostics["unknown_connectors"] == 2
    assert sum(connector["count"] for connector in evse_diagnostics["connectors"]) == 2
    assert evse_diagnostics["tariff"]["price_per_kwh"] == "0.42"
    unknown_diagnostics = next(
        station
        for station in diagnostics["stations"]
        if station["provider_station_id"] == "evse-unknown"
    )
    assert unknown_diagnostics["availability_reported"] is False
    assert unknown_diagnostics["evse_status"] == "UNKNOWN"
    assert unknown_diagnostics["available_connectors"] is None
    assert len(session.requests) == request_count
