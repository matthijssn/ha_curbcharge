"""Tests for diagnostics privacy."""

import json
from dataclasses import replace
from decimal import Decimal

import pytest
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.curbcharge.const import (
    CONF_API_TOKEN,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SEARCH_RADIUS,
    DOMAIN,
    LOCATION_MANUAL,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_NDW_DOT_NL,
)
from custom_components.curbcharge.coordinator import CurbChargeCoordinator
from custom_components.curbcharge.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.curbcharge.models import (
    ChargingLocation,
    ChargingTariff,
    EVSEStatus,
)
from tests.conftest import FakeProvider


@pytest.mark.asyncio
async def test_diagnostics_redact_coordinates_and_addresses(hass, sample_stations):
    station = replace(
        sample_stations[0],
        tariff=ChargingTariff("EUR", Decimal("0.42"), None, None, None, "NDW DOT-NL"),
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

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics)

    assert "52.123456" not in serialized
    assert "5.654321" not in serialized
    assert "Teststraat 1" not in serialized
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
    assert diagnostics["stations"][0]["tariff"]["price_per_kwh"] == "0.42"
    unknown_diagnostics = next(
        station
        for station in diagnostics["stations"]
        if station["provider_station_id"] == unknown.provider_id
    )
    assert unknown_diagnostics["availability_reported"] is False
    assert unknown_diagnostics["available_connectors"] is None
    assert len(provider.snapshots) == 1


@pytest.mark.asyncio
async def test_belgium_diagnostics_redact_nested_secrets_and_location(
    hass, sample_stations
):
    location = ChargingLocation(
        provider_id="site-private",
        provider=PROVIDER_ECO_MOVEMENT_BE,
        provider_name="Eco-Movement Belgium",
        country_code="BE",
        name="Private BE site",
        latitude=50.850123,
        longitude=4.351234,
        address="Private BE address",
        operator="Example CPO",
        last_updated=None,
    )
    station = replace(
        sample_stations[0],
        provider_id="evse-private",
        provider=PROVIDER_ECO_MOVEMENT_BE,
        provider_name="Eco-Movement Belgium",
        country_code="BE",
        name="Private BE EVSE",
        latitude=50.851234,
        longitude=4.352345,
        address="Private EVSE address",
        location=location,
        evse_id="BE*PRIVATE*EVSE",
        evse_status=EVSEStatus.AVAILABLE,
        tariff=ChargingTariff(
            "EUR", Decimal("0.42"), None, None, None, "Eco-Movement Belgium"
        ),
    )
    stale = replace(
        station,
        provider_id="evse-stale",
        evse_id="BE*STALE*EVSE",
        is_stale=True,
    )
    unknown = replace(
        sample_stations[2],
        provider_id="evse-unknown",
        provider=PROVIDER_ECO_MOVEMENT_BE,
        provider_name="Eco-Movement Belgium",
        country_code="BE",
        latitude=50.853210,
        longitude=4.353210,
        address="Private unknown EVSE address",
        location=location,
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
            CONF_API_TOKEN: "belgium-secret-token",
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

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics)

    for private_value in (
        "belgium-secret-token",
        "nested-belgium-secret-token",
        "belgium-options-secret-token",
        "nested-belgium-options-secret-token",
        "50.999999",
        "4.999999",
        "50.850123",
        "4.351234",
        "50.851234",
        "4.352345",
        "50.853210",
        "4.353210",
        "Private BE site",
        "Private BE address",
        "Private EVSE address",
        "Private unknown EVSE address",
    ):
        assert private_value not in serialized

    assert diagnostics["provider"] == "Eco-Movement Belgium"
    assert diagnostics["provider_id"] == PROVIDER_ECO_MOVEMENT_BE
    assert diagnostics["country_code"] == "BE"
    assert diagnostics["discovered_stations"] == 2
    assert diagnostics["retained_stations"] == 1
    assert len(diagnostics["stations"]) == 2
    evse_diagnostics = next(
        station
        for station in diagnostics["stations"]
        if station["provider_station_id"] == "evse-private"
    )
    assert evse_diagnostics["evse_status"] == "AVAILABLE"
    assert evse_diagnostics["available_connectors"] == 2
    assert evse_diagnostics["tariff"]["price_per_kwh"] == "0.42"
    unknown_diagnostics = next(
        station
        for station in diagnostics["stations"]
        if station["provider_station_id"] == "evse-unknown"
    )
    assert unknown_diagnostics["availability_reported"] is False
    assert unknown_diagnostics["evse_status"] == "UNKNOWN"
    assert unknown_diagnostics["available_connectors"] is None
    assert len(provider.snapshots) == 1
