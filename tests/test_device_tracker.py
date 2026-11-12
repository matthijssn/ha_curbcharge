"""Tests for CurbCharge station device trackers."""

from dataclasses import replace

import pytest
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import curbcharge
from custom_components.curbcharge.const import (
    CONF_DEVICE_TRACKERS,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SEARCH_RADIUS,
    DOMAIN,
    LOCATION_MANUAL,
    PROVIDER_NDW_DOT_NL,
)
from custom_components.curbcharge.coordinator import CurbChargeCoordinator
from tests.conftest import FakeProvider

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _entry(*, options: dict[str, object] | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="device-tracker-search-area",
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_LATITUDE: 52.0,
            CONF_LONGITUDE: 5.0,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
        },
        options=options or {},
    )


def _tracker_entries(hass, entry):
    return [
        registry_entry
        for registry_entry in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if registry_entry.domain == "device_tracker"
    ]


@pytest.mark.asyncio
async def test_trackers_follow_coordinates_and_unload(
    hass, monkeypatch, sample_stations
):
    station = sample_stations[0]
    moved = replace(station, latitude=station.latitude + 0.001)
    provider = FakeProvider([(station,), (moved,)])
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    tracker = _tracker_entries(hass, entry)[0]
    state = hass.states.get(tracker.entity_id)
    assert state is not None
    assert state.attributes["latitude"] == station.latitude
    assert state.attributes["longitude"] == station.longitude
    assert state.attributes["source_type"] == "gps"

    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(tracker.entity_id)
    assert state is not None
    assert state.attributes["latitude"] == moved.latitude
    assert state.attributes["longitude"] == moved.longitude

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(tracker.entity_id) is None


@pytest.mark.asyncio
async def test_disabled_trackers_are_not_forwarded(hass, monkeypatch, sample_stations):
    provider = FakeProvider([(sample_stations[0],)])
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry = _entry(options={CONF_DEVICE_TRACKERS: False})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _tracker_entries(hass, entry) == []
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_station_removal_removes_tracker(hass, monkeypatch, sample_stations):
    station = sample_stations[0]
    provider = FakeProvider([(station,), (), (), ()])
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    tracker = _tracker_entries(hass, entry)[0]

    for _ in range(3):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert hass.states.get(tracker.entity_id) is None
    assert _tracker_entries(hass, entry) == []
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_tracker_option_change_reloads_entry(hass, monkeypatch):
    entry = _entry(options={CONF_DEVICE_TRACKERS: False})
    entry.add_to_hass(hass)
    entry.runtime_data = CurbChargeCoordinator(
        hass,
        FakeProvider(),
        52.0,
        5.0,
        500,
        10,
        5,
        True,
        config_entry=entry,
    )
    reloaded: list[str] = []

    async def reload_entry(entry_id: str) -> None:
        reloaded.append(entry_id)

    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)
    await curbcharge._async_options_updated(hass, entry)

    assert reloaded == [entry.entry_id]
