"""Tests for diagnostics privacy."""

import json

import pytest
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.curbcharge.const import (
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SEARCH_RADIUS,
    DOMAIN,
    LOCATION_MANUAL,
    PROVIDER_NDW_DOT_NL,
)
from custom_components.curbcharge.coordinator import CurbChargeCoordinator
from custom_components.curbcharge.diagnostics import (
    async_get_config_entry_diagnostics,
)
from tests.conftest import FakeProvider


@pytest.mark.asyncio
async def test_diagnostics_redact_coordinates_and_addresses(hass, sample_stations):
    station = sample_stations[0]
    coordinator = CurbChargeCoordinator(
        hass, FakeProvider(), 52.0, 5.0, 500, 10, 5, True
    )
    coordinator.async_set_updated_data({station.provider_id: station})
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
        },
    )
    entry.runtime_data = coordinator

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    serialized = json.dumps(diagnostics)

    assert "52.123456" not in serialized
    assert "5.654321" not in serialized
    assert "Teststraat 1" not in serialized
    assert diagnostics["provider"] == "NDW DOT-NL"
    assert diagnostics["discovered_stations"] == 1
    assert diagnostics["stations"][0]["available_connectors"] == 2
    assert diagnostics["stations"][0]["distance_m_rounded"] == 0
