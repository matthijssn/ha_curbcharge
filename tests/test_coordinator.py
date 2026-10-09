"""Tests for coordinated data updates and station retention."""

from datetime import timedelta

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.curbcharge.coordinator import CurbChargeCoordinator
from custom_components.curbcharge.provider import (
    AuthenticationError,
    ProviderConnectionError,
    ProviderRateLimitError,
)
from tests.conftest import FakeProvider


def _coordinator(hass, provider, *, include_unknown=True):
    return CurbChargeCoordinator(
        hass,
        provider,
        52.0,
        5.0,
        500,
        10,
        5,
        include_unknown,
    )


@pytest.mark.asyncio
async def test_coordinator_updates_and_adds_new_stations(hass, sample_stations):
    provider = FakeProvider(
        [(sample_stations[0],), (sample_stations[0], sample_stations[1])]
    )
    coordinator = _coordinator(hass, provider)

    await coordinator.async_refresh()
    assert set(coordinator.data) == {sample_stations[0].provider_id}

    await coordinator.async_refresh()

    assert set(coordinator.data) == {
        sample_stations[0].provider_id,
        sample_stations[1].provider_id,
    }
    assert coordinator.last_successful_update is not None


@pytest.mark.asyncio
async def test_temporarily_missing_stations_are_retained_then_removed(
    hass, sample_stations
):
    station = sample_stations[0]
    provider = FakeProvider([(station,), (), (), ()])
    coordinator = _coordinator(hass, provider)

    await coordinator.async_refresh()
    for _ in (1, 2):
        await coordinator.async_refresh()
        assert coordinator.data[station.provider_id].is_stale
        assert station.provider_id not in coordinator.removed_station_ids

    await coordinator.async_refresh()

    assert station.provider_id not in coordinator.data
    assert coordinator.removed_station_ids == {station.provider_id}


@pytest.mark.asyncio
async def test_provider_failure_does_not_remove_cached_stations(hass, sample_stations):
    station = sample_stations[0]
    provider = FakeProvider([(station,), ProviderConnectionError("offline")])
    coordinator = _coordinator(hass, provider)
    await coordinator.async_refresh()

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert coordinator.data == {station.provider_id: station}
    assert coordinator.removed_station_ids == set()


@pytest.mark.asyncio
async def test_rate_limit_retry_after_extends_update_interval(hass):
    provider = FakeProvider([ProviderRateLimitError("limited", retry_after=600)])
    coordinator = _coordinator(hass, provider)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()

    assert coordinator.update_interval >= timedelta(seconds=600)


@pytest.mark.asyncio
async def test_authentication_failure_uses_home_assistant_exception(hass):
    coordinator = _coordinator(
        hass, FakeProvider([AuthenticationError("private details")])
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_unknown_status_option_excludes_stations_without_availability(
    hass, sample_stations
):
    provider = FakeProvider([sample_stations])
    coordinator = _coordinator(hass, provider, include_unknown=False)

    await coordinator.async_refresh()

    assert sample_stations[0].provider_id in coordinator.data
    assert sample_stations[1].provider_id in coordinator.data
    assert sample_stations[2].provider_id not in coordinator.data
