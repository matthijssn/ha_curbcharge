"""Tests for CurbCharge station device trackers."""

import builtins
import importlib
from dataclasses import replace

import pytest
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import curbcharge
from custom_components.curbcharge import device_tracker
from custom_components.curbcharge.const import (
    CONF_DEVICE_TRACKERS,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SEARCH_RADIUS,
    DOMAIN,
    LOCATION_MANUAL,
    OPTIONAL_PLATFORMS,
    PLATFORMS,
    PROVIDER_NDW_DOT_NL,
)
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


def _station_entity_ids(hass, entry):
    return {
        registry_entry.entity_id
        for registry_entry in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if registry_entry.domain in ("sensor", "binary_sensor")
    }


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
    state = hass.states.get(tracker.entity_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    assert state.attributes["restored"] is True


@pytest.mark.asyncio
async def test_disabled_trackers_are_not_forwarded(hass, monkeypatch, sample_stations):
    provider = FakeProvider([(sample_stations[0],)])
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry = _entry(options={CONF_DEVICE_TRACKERS: False})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _tracker_entries(hass, entry) == []

    unload_calls: list[tuple[str, ...]] = []
    unload_platforms = hass.config_entries.async_unload_platforms

    async def track_unload(entry, platforms):
        platforms = tuple(platforms)
        unload_calls.append(platforms)
        return await unload_platforms(entry, platforms)

    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", track_unload)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert unload_calls == [PLATFORMS]


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
async def test_tracker_option_toggles_without_reloading_or_refreshing(
    hass, monkeypatch, sample_stations
):
    station = sample_stations[0]
    provider = FakeProvider([(station,)])
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry = _entry(options={CONF_DEVICE_TRACKERS: False})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    sensor_entity_ids = _station_entity_ids(hass, entry)
    assert sensor_entity_ids
    assert provider.station_calls and len(provider.station_calls) == 1
    assert _tracker_entries(hass, entry) == []

    hass.config_entries.async_update_entry(entry, options={CONF_DEVICE_TRACKERS: True})
    await hass.async_block_till_done()

    tracker = _tracker_entries(hass, entry)[0]
    tracker_entity_id = tracker.entity_id
    assert entry.runtime_data is coordinator
    assert coordinator.device_trackers_enabled
    assert _station_entity_ids(hass, entry) == sensor_entity_ids
    assert hass.states.get(tracker_entity_id) is not None
    assert len(provider.station_calls) == 1

    hass.config_entries.async_update_entry(entry, options={CONF_DEVICE_TRACKERS: False})
    await hass.async_block_till_done()

    tracker_state = hass.states.get(tracker_entity_id)
    assert tracker_state is None or tracker_state.state == STATE_UNAVAILABLE
    assert entry.runtime_data is coordinator
    assert not coordinator.device_trackers_enabled
    assert _station_entity_ids(hass, entry) == sensor_entity_ids
    assert len(provider.station_calls) == 1

    hass.config_entries.async_update_entry(entry, options={CONF_DEVICE_TRACKERS: True})
    await hass.async_block_till_done()

    assert entry.runtime_data is coordinator
    assert coordinator.device_trackers_enabled
    assert _tracker_entries(hass, entry)[0].entity_id == tracker_entity_id
    assert _station_entity_ids(hass, entry) == sensor_entity_ids
    assert len(provider.station_calls) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_tracker_toggle_with_search_change_refreshes_once(
    hass, monkeypatch, sample_stations
):
    provider = FakeProvider([(sample_stations[0],), (sample_stations[0],)])
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry = _entry(options={CONF_DEVICE_TRACKERS: False})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert len(provider.station_calls) == 1

    hass.config_entries.async_update_entry(
        entry,
        options={CONF_DEVICE_TRACKERS: True, CONF_SEARCH_RADIUS: 600},
    )
    await hass.async_block_till_done()

    assert entry.runtime_data is coordinator
    assert coordinator.device_trackers_enabled
    assert coordinator.radius_m == 600
    assert len(provider.station_calls) == 2
    assert provider.station_calls[-1][2] == 600
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial", "updated", "operation"),
    [(False, True, "forward"), (True, False, "unload")],
)
async def test_tracker_platform_failure_reloads_entry(
    hass, monkeypatch, initial, updated, operation
):
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: FakeProvider())
    entry = _entry(options={CONF_DEVICE_TRACKERS: initial})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    reloads: list[str] = []

    async def reload_entry(entry_id: str) -> bool:
        reloads.append(entry_id)
        return True

    async def fail_forward(_entry, platforms):
        assert tuple(platforms) == OPTIONAL_PLATFORMS
        raise HomeAssistantError("platform setup failed")

    async def fail_unload(_entry, platforms):
        assert tuple(platforms) == OPTIONAL_PLATFORMS
        return False

    with monkeypatch.context() as patch:
        patch.setattr(hass.config_entries, "async_reload", reload_entry)
        if operation == "forward":
            patch.setattr(
                hass.config_entries, "async_forward_entry_setups", fail_forward
            )
        else:
            patch.setattr(hass.config_entries, "async_unload_platforms", fail_unload)
        hass.config_entries.async_update_entry(
            entry, options={CONF_DEVICE_TRACKERS: updated}
        )
        await hass.async_block_till_done()

    assert reloads == [entry.entry_id]
    assert coordinator.device_trackers_enabled is initial
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_tracker_import_falls_back_to_legacy_module(monkeypatch):
    original_import = builtins.__import__

    def import_without_tracker_entity(name, *args, **kwargs):
        if name == "homeassistant.components.device_tracker.entity":
            raise ImportError
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", import_without_tracker_entity)
        importlib.reload(device_tracker)

    importlib.reload(device_tracker)
    assert issubclass(device_tracker.StationDeviceTracker, device_tracker.TrackerEntity)
