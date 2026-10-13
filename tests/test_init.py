"""Tests for setup, unloading, and dynamically discovered stations."""

import asyncio
from dataclasses import replace
from pathlib import Path

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


def _entry(
    overrides: dict[str, object] | None = None,
    *,
    unique_id: str = "test-search-area",
    entry_id: str | None = None,
) -> MockConfigEntry:
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
        entry_id=entry_id,
        unique_id=unique_id,
        data=data,
    )


def _has_device(device_registry, entry_id: str, identifier: tuple[str, str]) -> bool:
    return any(
        identifier in device.identifiers
        for device in dr.async_entries_for_config_entry(device_registry, entry_id)
    )


@pytest.mark.asyncio
async def test_bundled_card_is_served_without_copying_to_www(
    hass, hass_client, monkeypatch, sample_stations
):
    provider = FakeProvider([(sample_stations[0],)])
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    client = await hass_client()

    response = await client.get("/curbcharge/curbcharge-card.js")

    assert response.status == 200
    card_path = Path(curbcharge.__file__).parent / "www" / "curbcharge-card.js"
    assert await response.read() == card_path.read_bytes()
    assert "Cache-Control" not in response.headers
    assert not (Path(hass.config.config_dir) / "www" / "curbcharge-card.js").exists()


def _provider_station(station, provider_id, provider_name, country_code):
    if provider_id == PROVIDER_ECO_MOVEMENT_BE:
        assert station.location is not None
        location = replace(
            station.location,
            provider_id="site-1",
            provider=provider_id,
            provider_name=provider_name,
            country_code=country_code,
            name="Example site",
        )
        return replace(
            station,
            provider_id="evse-1",
            evse_id="evse-1",
            provider=provider_id,
            provider_name=provider_name,
            country_code=country_code,
            location=location,
        )
    return replace(
        station,
        provider=provider_id,
        provider_name=provider_name,
        country_code=country_code,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_id", "provider_name", "country_code"),
    [
        (PROVIDER_NDW_DOT_NL, "NDW DOT-NL", "NL"),
        (PROVIDER_ECO_MOVEMENT_BE, "Eco-Movement Belgium", "BE"),
    ],
)
async def test_setup_unload_and_reload_keep_stable_entities(
    hass, monkeypatch, sample_stations, provider_id, provider_name, country_code
):
    station = _provider_station(
        sample_stations[0], provider_id, provider_name, country_code
    )

    provider = FakeProvider([(station,)])
    provider.provider_id = provider_id
    provider.provider_name = provider_name
    provider.country_code = country_code
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    overrides: dict[str, object] = {CONF_PROVIDER: provider_id}
    if provider_id == PROVIDER_ECO_MOVEMENT_BE:
        overrides[CONF_API_TOKEN] = "test-token"
    entry = _entry(overrides)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    station_identifier = station_device_identifier(provider_id, station.provider_id)
    station_device = next(
        device for device in devices if station_identifier in device.identifiers
    )
    assert (
        station_device.name == f"{station.name} ({provider_id}:{station.provider_id})"
    )

    if (
        station.location is not None
        and station.location.provider_id != station.provider_id
    ):
        parent_identifier = location_device_identifier(
            provider_id, station.location.provider_id
        )
        parent_device = next(
            device for device in devices if parent_identifier in device.identifiers
        )
        assert len(devices) == 2
        assert parent_device.name == (
            f"{station.location.name} ({provider_id}:{station.location.provider_id})"
        )
        assert (
            getattr(station_device, "parent_device_id", None) == parent_device.id
            or getattr(station_device, "via_device_id", None) == parent_device.id
        )
    else:
        assert len(devices) == 1

    before = {
        item.unique_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    expected_station_entities = {
        f"{provider_id}_{station.provider_id}_available",
        f"{provider_id}_{station.provider_id}_price_per_kwh",
        f"{provider_id}_{station.provider_id}_status_summary",
    }
    expected_connector_entities = {
        (
            f"{provider_id}_{station.provider_id}_connector_group_"
            f"{connector.provider_connector_id.rsplit(':', 1)[-1]}"
        )
        for connector in station.connectors
    }
    assert expected_station_entities <= before
    assert expected_connector_entities <= before

    available_entity = next(
        item
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.unique_id == f"{provider_id}_{station.provider_id}_available"
    )
    summary_entity = next(
        item
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.unique_id == f"{provider_id}_{station.provider_id}_status_summary"
    )
    summary_state = hass.states.get(summary_entity.entity_id)
    available_state = hass.states.get(available_entity.entity_id)
    assert summary_state is not None
    assert available_state is not None
    assert station.name in summary_state.attributes["friendly_name"]
    assert available_state.state == "on"

    coordinator = entry.runtime_data
    coordinator.async_set_updated_data(
        {station.provider_id: replace(station, is_stale=True)}
    )
    await hass.async_block_till_done()
    stale_state = hass.states.get(available_entity.entity_id)
    assert stale_state is not None
    assert stale_state.state == "unavailable"
    coordinator.async_set_updated_data({station.provider_id: station})
    await hass.async_block_till_done()
    restored_state = hass.states.get(available_entity.entity_id)
    assert restored_state is not None
    assert restored_state.state == "on"

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    after = {
        item.unique_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }

    assert before == after


@pytest.mark.asyncio
async def test_nl_and_be_entries_refresh_fail_recover_and_reload_independently(
    hass, monkeypatch, sample_stations
):
    nl_station = _provider_station(
        sample_stations[0], PROVIDER_NDW_DOT_NL, "NDW DOT-NL", "NL"
    )
    be_station = _provider_station(
        sample_stations[0],
        PROVIDER_ECO_MOVEMENT_BE,
        "Eco-Movement Belgium",
        "BE",
    )
    nl_provider = FakeProvider(
        [
            (nl_station,),
            ChargingProviderError("offline"),
            (nl_station,),
            (nl_station,),
            (nl_station,),
        ]
    )
    nl_provider.provider_id = PROVIDER_NDW_DOT_NL
    nl_provider.provider_name = "NDW DOT-NL"
    nl_provider.country_code = "NL"
    be_provider = FakeProvider(
        [
            (be_station,),
            (be_station,),
            ChargingProviderError("offline"),
            (be_station,),
            (be_station,),
        ]
    )
    be_provider.provider_id = PROVIDER_ECO_MOVEMENT_BE
    be_provider.provider_name = "Eco-Movement Belgium"
    be_provider.country_code = "BE"
    providers = {
        PROVIDER_NDW_DOT_NL: nl_provider,
        PROVIDER_ECO_MOVEMENT_BE: be_provider,
    }
    monkeypatch.setattr(
        curbcharge,
        "create_provider",
        lambda provider_id, *_args: providers[provider_id],
    )

    nl_entry = _entry(
        {CONF_PROVIDER: PROVIDER_NDW_DOT_NL},
        unique_id="nl-search-area",
        entry_id="nl-entry",
    )
    be_entry = _entry(
        {
            CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE,
            CONF_API_TOKEN: "test-token",
        },
        unique_id="be-search-area",
        entry_id="be-entry",
    )
    assert nl_entry.entry_id != be_entry.entry_id
    nl_entry.add_to_hass(hass)
    be_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(nl_entry.entry_id)
    await hass.async_block_till_done()
    assert nl_entry.runtime_data.provider_id == PROVIDER_NDW_DOT_NL
    assert be_entry.runtime_data.provider_id == PROVIDER_ECO_MOVEMENT_BE

    registry = er.async_get(hass)
    nl_entities = er.async_entries_for_config_entry(registry, nl_entry.entry_id)
    be_entities = er.async_entries_for_config_entry(registry, be_entry.entry_id)
    nl_unique_ids = {entity.unique_id for entity in nl_entities}
    be_unique_ids = {entity.unique_id for entity in be_entities}
    assert nl_unique_ids.isdisjoint(be_unique_ids)

    nl_count = next(
        entity
        for entity in nl_entities
        if entity.unique_id == "nl-search-area_nearby_charging_units"
    )
    be_count = next(
        entity
        for entity in be_entities
        if entity.unique_id == "be-search-area_nearby_charging_units"
    )
    nl_available_connectors = next(
        entity
        for entity in nl_entities
        if entity.unique_id == "nl-search-area_nearby_available_connectors"
    )
    be_available_connectors = next(
        entity
        for entity in be_entities
        if entity.unique_id == "be-search-area_nearby_available_connectors"
    )
    nl_availability_percentage = next(
        entity
        for entity in nl_entities
        if entity.unique_id == "nl-search-area_nearby_availability_percentage"
    )
    be_availability_percentage = next(
        entity
        for entity in be_entities
        if entity.unique_id == "be-search-area_nearby_availability_percentage"
    )
    nl_available = next(
        entity
        for entity in nl_entities
        if entity.unique_id
        == f"{PROVIDER_NDW_DOT_NL}_{nl_station.provider_id}_available"
    )
    be_available = next(
        entity
        for entity in be_entities
        if entity.unique_id
        == f"{PROVIDER_ECO_MOVEMENT_BE}_{be_station.provider_id}_available"
    )
    nl_count_state = hass.states.get(nl_count.entity_id)
    be_count_state = hass.states.get(be_count.entity_id)
    nl_available_connectors_state = hass.states.get(nl_available_connectors.entity_id)
    be_available_connectors_state = hass.states.get(be_available_connectors.entity_id)
    nl_percentage_state = hass.states.get(nl_availability_percentage.entity_id)
    be_percentage_state = hass.states.get(be_availability_percentage.entity_id)
    assert nl_count_state is not None and nl_count_state.state == "1"
    assert be_count_state is not None and be_count_state.state == "1"
    assert (
        nl_available_connectors_state is not None
        and nl_available_connectors_state.state == "2"
    )
    assert (
        be_available_connectors_state is not None
        and be_available_connectors_state.state == "2"
    )
    assert nl_percentage_state is not None
    assert float(nl_percentage_state.state) == 50.0
    assert be_percentage_state is not None
    assert float(be_percentage_state.state) == 50.0
    assert nl_count_state.attributes["country_code"] == "NL"
    assert be_count_state.attributes["country_code"] == "BE"
    assert nl_count_state.attributes["provider_id"] == PROVIDER_NDW_DOT_NL
    assert be_count_state.attributes["provider_id"] == PROVIDER_ECO_MOVEMENT_BE
    assert nl_count_state.attributes["supported_country_codes"] == ["NL", "BE"]
    assert be_count_state.attributes["supported_country_codes"] == ["NL", "BE"]
    assert nl_count_state.attributes["coverage_scope"] == "configured_search_area"
    assert be_count_state.attributes["coverage_scope"] == "configured_search_area"

    nl_coordinator = nl_entry.runtime_data
    be_coordinator = be_entry.runtime_data
    assert set(nl_coordinator.data) == {nl_station.provider_id}
    assert set(be_coordinator.data) == {be_station.provider_id}

    await asyncio.gather(
        nl_coordinator.async_refresh(),
        be_coordinator.async_refresh(),
    )
    await hass.async_block_till_done()
    assert not nl_coordinator.last_update_success
    assert be_coordinator.last_update_success
    assert hass.states.get(nl_count.entity_id).state == "unavailable"
    assert hass.states.get(nl_available_connectors.entity_id).state == "unavailable"
    assert hass.states.get(nl_availability_percentage.entity_id).state == "unavailable"
    assert hass.states.get(nl_available.entity_id).state == "unavailable"
    assert hass.states.get(be_count.entity_id).state == "1"
    assert hass.states.get(be_available_connectors.entity_id).state == "2"
    assert float(hass.states.get(be_availability_percentage.entity_id).state) == 50.0
    assert hass.states.get(be_available.entity_id).state == "on"

    await asyncio.gather(
        nl_coordinator.async_refresh(),
        be_coordinator.async_refresh(),
    )
    await hass.async_block_till_done()
    assert nl_coordinator.last_update_success
    assert not be_coordinator.last_update_success
    assert hass.states.get(nl_count.entity_id).state == "1"
    assert hass.states.get(nl_available_connectors.entity_id).state == "2"
    assert float(hass.states.get(nl_availability_percentage.entity_id).state) == 50.0
    assert hass.states.get(nl_available.entity_id).state == "on"
    assert hass.states.get(be_count.entity_id).state == "unavailable"
    assert hass.states.get(be_available_connectors.entity_id).state == "unavailable"
    assert hass.states.get(be_availability_percentage.entity_id).state == "unavailable"
    assert hass.states.get(be_available.entity_id).state == "unavailable"

    await asyncio.gather(
        nl_coordinator.async_refresh(),
        be_coordinator.async_refresh(),
    )
    await hass.async_block_till_done()
    assert nl_coordinator.last_update_success
    assert be_coordinator.last_update_success
    assert hass.states.get(nl_count.entity_id).state == "1"
    assert hass.states.get(be_count.entity_id).state == "1"
    assert hass.states.get(nl_available_connectors.entity_id).state == "2"
    assert hass.states.get(be_available_connectors.entity_id).state == "2"
    assert float(hass.states.get(nl_availability_percentage.entity_id).state) == 50.0
    assert float(hass.states.get(be_availability_percentage.entity_id).state) == 50.0

    assert await hass.config_entries.async_unload(nl_entry.entry_id)
    await hass.async_block_till_done()
    nl_unloaded_count = hass.states.get(nl_count.entity_id)
    assert nl_unloaded_count is not None
    assert nl_unloaded_count.state == "unavailable"
    nl_unloaded_available_connectors = hass.states.get(
        nl_available_connectors.entity_id
    )
    assert nl_unloaded_available_connectors is not None
    assert nl_unloaded_available_connectors.state == "unavailable"
    nl_unloaded_percentage = hass.states.get(nl_availability_percentage.entity_id)
    assert nl_unloaded_percentage is not None
    assert nl_unloaded_percentage.state == "unavailable"
    assert hass.states.get(be_count.entity_id).state == "1"
    assert hass.states.get(be_available_connectors.entity_id).state == "2"
    assert float(hass.states.get(be_availability_percentage.entity_id).state) == 50.0
    assert be_coordinator.last_update_success

    assert await hass.config_entries.async_setup(nl_entry.entry_id)
    await hass.async_block_till_done()
    assert {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(registry, nl_entry.entry_id)
    } == nl_unique_ids
    assert hass.states.get(nl_count.entity_id).state == "1"
    assert hass.states.get(be_count.entity_id).state == "1"
    assert hass.states.get(nl_available_connectors.entity_id).state == "2"
    assert hass.states.get(be_available_connectors.entity_id).state == "2"
    assert float(hass.states.get(nl_availability_percentage.entity_id).state) == 50.0
    assert float(hass.states.get(be_availability_percentage.entity_id).state) == 50.0

    assert await hass.config_entries.async_unload(be_entry.entry_id)
    await hass.async_block_till_done()
    be_unloaded_count = hass.states.get(be_count.entity_id)
    assert be_unloaded_count is not None
    assert be_unloaded_count.state == "unavailable"
    be_unloaded_available_connectors = hass.states.get(
        be_available_connectors.entity_id
    )
    assert be_unloaded_available_connectors is not None
    assert be_unloaded_available_connectors.state == "unavailable"
    be_unloaded_percentage = hass.states.get(be_availability_percentage.entity_id)
    assert be_unloaded_percentage is not None
    assert be_unloaded_percentage.state == "unavailable"
    assert hass.states.get(nl_count.entity_id).state == "1"
    assert hass.states.get(nl_available_connectors.entity_id).state == "2"
    assert float(hass.states.get(nl_availability_percentage.entity_id).state) == 50.0

    assert await hass.config_entries.async_setup(be_entry.entry_id)
    await hass.async_block_till_done()
    assert {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(registry, be_entry.entry_id)
    } == be_unique_ids
    assert hass.states.get(be_count.entity_id).state == "1"
    assert hass.states.get(nl_count.entity_id).state == "1"
    assert hass.states.get(be_available_connectors.entity_id).state == "2"
    assert hass.states.get(nl_available_connectors.entity_id).state == "2"
    assert float(hass.states.get(be_availability_percentage.entity_id).state) == 50.0
    assert float(hass.states.get(nl_availability_percentage.entity_id).state) == 50.0


@pytest.mark.asyncio
async def test_same_country_entries_keep_coverage_counts_per_search_area(
    hass, monkeypatch, sample_stations
):
    first, second = sample_stations[:2]
    providers = iter((FakeProvider([(first,)]), FakeProvider([(second,)])))
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: next(providers))

    first_entry = _entry(
        unique_id="nl-area-one",
        entry_id="nl-area-one-entry",
    )
    first_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(first_entry.entry_id)
    await hass.async_block_till_done()

    second_entry = _entry(
        unique_id="nl-area-two",
        entry_id="nl-area-two-entry",
    )
    second_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second_entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    first_entities = er.async_entries_for_config_entry(registry, first_entry.entry_id)
    second_entities = er.async_entries_for_config_entry(registry, second_entry.entry_id)
    first_unique_ids = {entity.unique_id for entity in first_entities}
    second_unique_ids = {entity.unique_id for entity in second_entities}
    assert first_unique_ids.isdisjoint(second_unique_ids)

    first_count = next(
        entity
        for entity in first_entities
        if entity.unique_id == "nl-area-one_nearby_charging_units"
    )
    second_count = next(
        entity
        for entity in second_entities
        if entity.unique_id == "nl-area-two_nearby_charging_units"
    )
    first_state = hass.states.get(first_count.entity_id)
    second_state = hass.states.get(second_count.entity_id)
    assert first_state is not None and first_state.state == "1"
    assert second_state is not None and second_state.state == "1"
    assert first_state.attributes["country_code"] == "NL"
    assert second_state.attributes["country_code"] == "NL"
    assert first_state.attributes["coverage_scope"] == "configured_search_area"
    assert second_state.attributes["coverage_scope"] == "configured_search_area"

    first_coordinator = first_entry.runtime_data
    second_coordinator = second_entry.runtime_data
    assert set(first_coordinator.data) == {first.provider_id}
    assert set(second_coordinator.data) == {second.provider_id}


@pytest.mark.asyncio
async def test_legacy_netherlands_entry_loads_without_provider_field(
    hass, monkeypatch, sample_stations
):
    station = sample_stations[0]
    provider = FakeProvider([(station,)])
    calls: list[tuple[object, ...]] = []

    def create_provider(*args):
        calls.append(args)
        return provider

    monkeypatch.setattr(curbcharge, "create_provider", create_provider)
    legacy_data = dict(_entry().data)
    legacy_data.pop(CONF_PROVIDER)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="legacy-netherlands-entry",
        data=legacy_data,
    )
    old_unique_id = entry.unique_id
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device_identifier = (
        DOMAIN,
        f"{PROVIDER_NDW_DOT_NL}:{station.provider_id}",
    )
    assert calls[0][0] == PROVIDER_NDW_DOT_NL
    assert entry.runtime_data.provider_id == PROVIDER_NDW_DOT_NL
    assert entry.unique_id == old_unique_id
    assert _has_device(dr.async_get(hass), entry.entry_id, device_identifier)
    assert CONF_PROVIDER not in entry.data


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
