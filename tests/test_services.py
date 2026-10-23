"""Tests for favorite management services."""

from dataclasses import replace

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.curbcharge as curbcharge
from custom_components.curbcharge import services
from custom_components.curbcharge.const import (
    CONF_FAVOURITES,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SEARCH_RADIUS,
    DOMAIN,
    LOCATION_MANUAL,
    PROVIDER_NDW_DOT_NL,
)
from custom_components.curbcharge.services import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_STATION_ID,
    SERVICE_ADD_FAVORITE,
    SERVICE_REMOVE_FAVORITE,
)
from tests.conftest import FakeProvider

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _entry(
    *,
    unique_id: str = "favorite-services",
    options: dict[str, object] | None = None,
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_LATITUDE: 52.0,
            CONF_LONGITUDE: 5.0,
        },
        options=options or {},
    )


async def _setup_entry(hass, monkeypatch, snapshots):
    provider = FakeProvider(snapshots)
    monkeypatch.setattr(curbcharge, "create_provider", lambda *_args: provider)
    entry = _entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    return entry, provider


async def _call_service(
    hass,
    service: str,
    entry_id: str,
    station_id: str,
    *,
    context: Context | None = None,
) -> None:
    await hass.services.async_call(
        DOMAIN,
        service,
        {
            ATTR_CONFIG_ENTRY_ID: entry_id,
            ATTR_STATION_ID: station_id,
        },
        blocking=True,
        context=context,
    )


def test_config_schema_is_config_entry_only():
    assert curbcharge.CONFIG_SCHEMA({}) == {}


@pytest.mark.asyncio
async def test_add_and_remove_favorite_without_provider_requests(
    hass, monkeypatch, sample_stations
):
    station, second_station = sample_stations[:2]
    entry, provider = await _setup_entry(hass, monkeypatch, [(station, second_station)])
    coordinator = entry.runtime_data

    assert SERVICE_ADD_FAVORITE in hass.services.async_services()[DOMAIN]
    assert SERVICE_REMOVE_FAVORITE in hass.services.async_services()[DOMAIN]
    assert len(provider.station_calls) == 1

    await _call_service(hass, SERVICE_ADD_FAVORITE, entry.entry_id, station.provider_id)
    await _call_service(
        hass, SERVICE_ADD_FAVORITE, entry.entry_id, second_station.provider_id
    )
    await _call_service(hass, SERVICE_ADD_FAVORITE, entry.entry_id, station.provider_id)
    await hass.async_block_till_done()

    assert {
        favourite["provider_id"] for favourite in entry.options[CONF_FAVOURITES]
    } == {station.provider_id, second_station.provider_id}
    assert coordinator.favourite_ids == {
        station.provider_id,
        second_station.provider_id,
    }
    assert len(provider.station_calls) == 1

    await _call_service(hass, SERVICE_ADD_FAVORITE, entry.entry_id, station.provider_id)
    await hass.async_block_till_done()

    assert len(entry.options[CONF_FAVOURITES]) == 2
    assert len(provider.station_calls) == 1

    coordinator.async_set_updated_data(
        {
            station.provider_id: replace(station, is_stale=True),
            second_station.provider_id: second_station,
        }
    )
    await hass.async_block_till_done()

    await _call_service(
        hass, SERVICE_REMOVE_FAVORITE, entry.entry_id, station.provider_id
    )
    await hass.async_block_till_done()

    assert [
        favourite["provider_id"] for favourite in entry.options[CONF_FAVOURITES]
    ] == [second_station.provider_id]
    assert coordinator.favourite_ids == {second_station.provider_id}
    assert len(provider.station_calls) == 1


@pytest.mark.asyncio
async def test_favorite_services_validate_station_ids(
    hass, monkeypatch, sample_stations
):
    station = sample_stations[0]
    entry, _provider = await _setup_entry(hass, monkeypatch, [(station,)])
    coordinator = entry.runtime_data
    coordinator.async_set_updated_data(
        {station.provider_id: replace(station, is_stale=True)}
    )
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await _call_service(
            hass, SERVICE_ADD_FAVORITE, entry.entry_id, station.provider_id
        )
    assert err.value.translation_key == "station_not_discovered"

    with pytest.raises(ServiceValidationError) as err:
        await _call_service(hass, SERVICE_ADD_FAVORITE, entry.entry_id, "unknown")
    assert err.value.translation_key == "station_not_discovered"

    with pytest.raises(ServiceValidationError) as err:
        await _call_service(hass, SERVICE_ADD_FAVORITE, entry.entry_id, "  ")
    assert err.value.translation_key == "invalid_station_id"

    with pytest.raises(ServiceValidationError) as err:
        await _call_service(
            hass, SERVICE_REMOVE_FAVORITE, entry.entry_id, station.provider_id
        )
    assert err.value.translation_key == "favorite_not_found"

    with pytest.raises(ServiceValidationError) as err:
        services._entry_favourites(
            _entry(options={CONF_FAVOURITES: "invalid"}), coordinator
        )
    assert err.value.translation_key == "invalid_config"


@pytest.mark.asyncio
async def test_favorite_services_require_loaded_curbcharge_entry(hass, monkeypatch):
    loaded_entry, _provider = await _setup_entry(hass, monkeypatch, [()])
    unloaded_entry = _entry(unique_id="unloaded-favorite-services")
    unloaded_entry.add_to_hass(hass)
    other_entry = MockConfigEntry(domain="other", unique_id="other-domain")
    other_entry.add_to_hass(hass)

    for entry_id in (
        "missing-entry",
        unloaded_entry.entry_id,
        other_entry.entry_id,
    ):
        with pytest.raises(ServiceValidationError) as err:
            await _call_service(hass, SERVICE_ADD_FAVORITE, entry_id, "station-id")
        assert err.value.translation_key == "config_entry_not_loaded"

    assert loaded_entry.state is ConfigEntryState.LOADED


@pytest.mark.asyncio
async def test_favorite_services_are_admin_only(hass, monkeypatch):
    entry, _provider = await _setup_entry(hass, monkeypatch, [()])

    with pytest.raises(HomeAssistantError):
        await _call_service(
            hass,
            SERVICE_ADD_FAVORITE,
            entry.entry_id,
            "station-id",
            context=Context(user_id="not-an-admin"),
        )
