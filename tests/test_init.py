"""Tests for setup, unloading, and dynamically discovered stations."""

import pytest
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.curbcharge as curbcharge
from custom_components.curbcharge.const import (
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SEARCH_RADIUS,
    DOMAIN,
    LOCATION_MANUAL,
    PROVIDER_NDW_DOT_NL,
)
from tests.conftest import FakeProvider

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-search-area",
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_LATITUDE: 52.0,
            CONF_LONGITUDE: 5.0,
        },
    )


@pytest.mark.asyncio
async def test_setup_unload_and_reload_keep_stable_entities(
    hass, monkeypatch, sample_stations
):
    provider = FakeProvider([(sample_stations[0],)])
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    before = {
        item.unique_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert before

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    after = {
        item.unique_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }

    assert before == after


@pytest.mark.asyncio
async def test_new_stations_are_added_and_removed_after_three_successful_polls(
    hass, monkeypatch, sample_stations
):
    first, newly_discovered = sample_stations[:2]
    provider = FakeProvider(
        [(first,), (first, newly_discovered), (first,), (first,), (first,)]
    )
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    device_registry = dr.async_get(hass)
    identifier = (
        DOMAIN,
        f"{newly_discovered.provider}:{newly_discovered.provider_id}",
    )

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    device = device_registry.async_get_device_by_identifier(identifier, entry.entry_id)
    assert device is not None

    await coordinator.async_refresh()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert (
        device_registry.async_get_device_by_identifier(identifier, entry.entry_id)
        is not None
    )

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert (
        device_registry.async_get_device_by_identifier(identifier, entry.entry_id)
        is None
    )
