"""Tests for setup, unloading, and dynamically discovered stations."""

from dataclasses import replace

import pytest
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.curbcharge as curbcharge
from custom_components.curbcharge.const import (
    CONF_API_TOKEN,
    CONF_INCLUDE_UNKNOWN,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH_RADIUS,
    DOMAIN,
    LOCATION_HOME,
    LOCATION_MANUAL,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_NDW_DOT_NL,
)
from custom_components.curbcharge.entity import (
    location_device_identifier,
    station_device_identifier,
)
from custom_components.curbcharge.provider import ChargingProviderError
from tests.conftest import FakeProvider

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _entry(overrides: dict[str, object] | None = None) -> MockConfigEntry:
    data: dict[str, object] = {
        CONF_LOCATION_SOURCE: LOCATION_MANUAL,
        CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        CONF_SEARCH_RADIUS: 500,
        CONF_MAX_STATIONS: 10,
        CONF_LATITUDE: 52.0,
        CONF_LONGITUDE: 5.0,
    }
    if overrides is not None:
        data.update(overrides)
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="test-search-area",
        data=data,
    )


def _has_device(device_registry, entry_id: str, identifier: tuple[str, str]) -> bool:
    return any(
        identifier in device.identifiers
        for device in dr.async_entries_for_config_entry(device_registry, entry_id)
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
    assert (
        len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1
    )
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
    assert _has_device(device_registry, entry.entry_id, identifier)

    await coordinator.async_refresh()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert _has_device(device_registry, entry.entry_id, identifier)

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert not _has_device(device_registry, entry.entry_id, identifier)


@pytest.mark.asyncio
async def test_stations_with_the_same_address_get_unique_device_names(
    hass, monkeypatch, sample_stations
):
    first, second = sample_stations[:2]
    second = replace(second, name=first.name, address=first.address)
    provider = FakeProvider([(first, second)])
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    names = [device.name for device in devices]

    assert len(devices) == 2
    assert len(set(names)) == 2
    assert set(names) == {
        f"{station.name} ({station.provider}:{station.provider_id})"
        for station in (first, second)
    }


@pytest.mark.asyncio
async def test_belgium_evse_devices_are_children_of_location_and_cleanup_parent(
    hass, monkeypatch, sample_stations
):
    sample_station = sample_stations[0]
    assert sample_station.location is not None
    location = replace(
        sample_station.location,
        provider_id="site-1",
        provider=PROVIDER_ECO_MOVEMENT_BE,
        provider_name="Eco-Movement Belgium",
        country_code="BE",
        name="Example charging site",
    )
    first_evse = replace(
        sample_station,
        provider_id="evse-1",
        name="EVSE 1",
        provider=PROVIDER_ECO_MOVEMENT_BE,
        provider_name="Eco-Movement Belgium",
        country_code="BE",
        location=location,
        evse_id=None,
    )
    second_evse = replace(
        first_evse,
        provider_id="evse-2",
        name="EVSE 2",
    )
    provider = FakeProvider(
        [
            (first_evse, second_evse),
            (second_evse,),
            (second_evse,),
            (second_evse,),
            (),
            (),
            (),
        ]
    )
    provider.provider_id = PROVIDER_ECO_MOVEMENT_BE
    provider.provider_name = "Eco-Movement Belgium"
    provider.country_code = "BE"
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry = _entry(
        {CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE, CONF_API_TOKEN: "test-token"}
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    device_registry = dr.async_get(hass)
    parent_identifier = location_device_identifier(
        PROVIDER_ECO_MOVEMENT_BE, location.provider_id
    )
    parent_device = next(
        device
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
        if parent_identifier in device.identifiers
    )

    def evse_device(evse_id: str):
        identifier = station_device_identifier(PROVIDER_ECO_MOVEMENT_BE, evse_id)
        get_child = getattr(
            device_registry, "async_get_child_device_by_identifier", None
        )
        child = get_child(identifier, entry.entry_id) if get_child is not None else None
        if child is not None:
            return child
        return next(
            (
                device
                for device in dr.async_entries_for_config_entry(
                    device_registry, entry.entry_id
                )
                if identifier in device.identifiers
            ),
            None,
        )

    first_device = evse_device("evse-1")
    second_device = evse_device("evse-2")
    assert first_device is not None
    assert second_device is not None
    assert getattr(first_device, "parent_device_id", None) == parent_device.id or (
        getattr(first_device, "via_device_id", None) == parent_device.id
    )
    assert getattr(second_device, "parent_device_id", None) == parent_device.id or (
        getattr(second_device, "via_device_id", None) == parent_device.id
    )

    for _ in range(3):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert evse_device("evse-1") is None
    assert evse_device("evse-2") is not None
    assert parent_device in dr.async_entries_for_config_entry(
        device_registry, entry.entry_id
    )

    for _ in range(3):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert evse_device("evse-2") is None
    assert parent_identifier not in {
        identifier
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
        for identifier in device.identifiers
    }


def test_entry_location_rejects_invalid_or_incomplete_coordinates(hass):
    invalid_locations = (
        {CONF_LOCATION_SOURCE: LOCATION_MANUAL},
        {CONF_LATITUDE: True, CONF_LONGITUDE: 5},
        {CONF_LATITUDE: float("nan"), CONF_LONGITUDE: 5},
        {CONF_LATITUDE: 91, CONF_LONGITUDE: 5},
        {CONF_LATITUDE: 52, CONF_LONGITUDE: 181},
    )
    for location in invalid_locations:
        assert curbcharge._entry_location(hass, location) is None

    hass.config.latitude = 0
    hass.config.longitude = 0
    assert (
        curbcharge._entry_location(hass, {CONF_LOCATION_SOURCE: LOCATION_HOME}) is None
    )

    hass.config.latitude = 52
    hass.config.longitude = 5
    assert curbcharge._entry_location(hass, {CONF_LOCATION_SOURCE: LOCATION_HOME}) == (
        52.0,
        5.0,
    )


@pytest.mark.asyncio
async def test_setup_requires_a_valid_location(hass):
    entry = _entry({CONF_LATITUDE: None, CONF_LONGITUDE: None})

    with pytest.raises(ConfigEntryNotReady) as error:
        await curbcharge.async_setup_entry(hass, entry)

    assert error.value.translation_key == "location_not_configured"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value"),
    [
        (CONF_PROVIDER, "unsupported"),
        (CONF_SEARCH_RADIUS, 1),
        (CONF_MAX_STATIONS, 0),
        (CONF_SCAN_INTERVAL, 0),
        (CONF_INCLUDE_UNKNOWN, 1),
    ],
)
async def test_setup_rejects_invalid_options(hass, key, value):
    entry = _entry({key: value})

    with pytest.raises(ConfigEntryNotReady) as error:
        await curbcharge.async_setup_entry(hass, entry)

    assert error.value.translation_key == "invalid_config"


@pytest.mark.asyncio
async def test_setup_requires_a_belgium_token(hass):
    entry = _entry({CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE})

    with pytest.raises(ConfigEntryNotReady) as error:
        await curbcharge.async_setup_entry(hass, entry)

    assert error.value.translation_key == "invalid_config"


@pytest.mark.asyncio
async def test_setup_rejects_a_malformed_belgium_token(hass):
    entry = _entry(
        {
            CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE,
            CONF_API_TOKEN: "bad\nheader",
        }
    )

    with pytest.raises(ConfigEntryNotReady) as error:
        await curbcharge.async_setup_entry(hass, entry)

    assert error.value.translation_key == "invalid_config"


@pytest.mark.asyncio
async def test_setup_passes_belgium_token_to_provider_factory(hass, monkeypatch):
    token = "test-belgium-token"
    entry = _entry(
        {
            CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE,
            CONF_API_TOKEN: token,
        }
    )
    provider = FakeProvider([()])
    calls: list[tuple[object, ...]] = []

    def create_provider(*args):
        calls.append(args)
        return provider

    monkeypatch.setattr(curbcharge, "create_provider", create_provider)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert calls[0][0] == PROVIDER_ECO_MOVEMENT_BE
    assert calls[0][2:] == (hass, token)
    assert entry.runtime_data.provider_id == PROVIDER_NDW_DOT_NL


@pytest.mark.asyncio
async def test_setup_translates_provider_initialization_error(hass, monkeypatch):
    entry = _entry()

    def fail_provider(*_args):
        raise ChargingProviderError("private provider details")

    monkeypatch.setattr(curbcharge, "create_provider", fail_provider)

    with pytest.raises(ConfigEntryNotReady) as error:
        await curbcharge.async_setup_entry(hass, entry)

    assert error.value.translation_key == "unsupported_provider"


@pytest.mark.asyncio
async def test_setup_translates_first_refresh_failure(hass, monkeypatch):
    entry = _entry()
    provider = FakeProvider([ChargingProviderError("private provider details")])
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)


@pytest.mark.asyncio
async def test_options_update_reloads_the_entry(hass, monkeypatch):
    entry = _entry()
    reloaded: list[str] = []

    async def reload_entry(entry_id: str) -> None:
        reloaded.append(entry_id)

    monkeypatch.setattr(hass.config_entries, "async_reload", reload_entry)

    await curbcharge._async_options_updated(hass, entry)

    assert reloaded == [entry.entry_id]
