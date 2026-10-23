"""Tests for coordinated data updates and station retention."""

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.curbcharge.const import (
    DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M,
    DOMAIN,
)
from custom_components.curbcharge.coordinator import CurbChargeCoordinator
from custom_components.curbcharge.geography import distance_m
from custom_components.curbcharge.models import (
    DiscoveryFilterSettings,
    FavouriteStationReference,
    ProviderTimestampSource,
    StationDiscoveryResult,
)
from custom_components.curbcharge.provider import (
    AuthenticationError,
    ProviderConnectionError,
    ProviderRateLimitError,
)
from tests.conftest import FakeProvider


def _coordinator(
    hass,
    provider,
    *,
    include_unknown=True,
    favourites=(),
    location_resolver=None,
    location_movement_threshold_m=DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M,
    discovery_filters=None,
):
    return CurbChargeCoordinator(
        hass,
        provider,
        52.0,
        5.0,
        500,
        10,
        5,
        include_unknown,
        favourites,
        location_resolver=location_resolver,
        location_movement_threshold_m=location_movement_threshold_m,
        discovery_filters=discovery_filters,
    )


def _favourite(station):
    return FavouriteStationReference(
        provider=station.provider,
        provider_id=station.provider_id,
        name=station.provider_id,
        latitude=station.latitude,
        longitude=station.longitude,
    )


@pytest.mark.asyncio
async def test_coordinator_updates_and_adds_new_stations(hass, sample_stations):
    provider = FakeProvider(
        [(sample_stations[0],), (sample_stations[0], sample_stations[1])]
    )
    coordinator = _coordinator(hass, provider)

    assert coordinator.provider_id == "ndw_dot_nl"
    assert coordinator.provider_name == "NDW DOT-NL"
    assert coordinator.country_code == "NL"

    await coordinator.async_refresh()
    assert set(coordinator.data) == {sample_stations[0].provider_id}

    await coordinator.async_refresh()

    assert set(coordinator.data) == {
        sample_stations[0].provider_id,
        sample_stations[1].provider_id,
    }
    assert coordinator.last_successful_update is not None


@pytest.mark.asyncio
async def test_coordinator_passes_ndw_filters_and_unknown_setting(
    hass, sample_stations
):
    provider = FakeProvider([(sample_stations[0],)])
    filters = DiscoveryFilterSettings(
        connector_types=("IEC_62196_T2",),
        minimum_power_kw=11.0,
        require_available=True,
    )
    coordinator = _coordinator(
        hass,
        provider,
        include_unknown=False,
        discovery_filters=filters,
    )

    await coordinator.async_refresh()

    assert provider.discovery_filter_calls == [(filters, False)]


@pytest.mark.asyncio
async def test_temporarily_missing_stations_are_retained_then_removed(
    hass, sample_stations
):
    station = replace(
        sample_stations[0],
        last_updated=datetime(2020, 1, 1, tzinfo=UTC),
        last_updated_source=ProviderTimestampSource.NDW_FEATURE,
    )
    assert station.provider_data_age_seconds(datetime(2025, 1, 1, tzinfo=UTC)) > 0
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
    station = replace(
        sample_stations[0],
        last_updated=datetime(2020, 1, 1, tzinfo=UTC),
        last_updated_source=ProviderTimestampSource.NDW_FEATURE,
    )
    assert station.provider_data_age_seconds(datetime(2025, 1, 1, tzinfo=UTC)) > 0
    provider = FakeProvider([(station,), ProviderConnectionError("offline")])
    coordinator = _coordinator(hass, provider)
    await coordinator.async_refresh()

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert coordinator.data == {station.provider_id: station}
    assert coordinator.removed_station_ids == set()


@pytest.mark.asyncio
async def test_tracked_location_applies_threshold_from_last_accepted_center(hass):
    initial = (52.0, 5.0)
    subthreshold = (52.0005, 5.0)
    beyond_threshold = (52.0011, 5.0)
    current_location = initial
    provider = FakeProvider([(), (), ()])
    coordinator = _coordinator(
        hass,
        provider,
        location_resolver=lambda: current_location,
        location_movement_threshold_m=100,
    )

    assert distance_m(*initial, *subthreshold) < 100
    assert distance_m(*initial, *beyond_threshold) >= 100
    assert distance_m(*subthreshold, *beyond_threshold) < 100

    await coordinator.async_refresh()
    current_location = subthreshold
    await coordinator.async_refresh()
    current_location = beyond_threshold
    await coordinator.async_refresh()

    assert [call[:2] for call in provider.station_calls] == [
        initial,
        initial,
        beyond_threshold,
    ]


@pytest.mark.asyncio
async def test_zero_movement_threshold_accepts_any_new_location(hass):
    initial = (52.0, 5.0)
    moved = (52.00001, 5.0)
    current_location = initial
    provider = FakeProvider([(), ()])
    coordinator = _coordinator(
        hass,
        provider,
        location_resolver=lambda: current_location,
        location_movement_threshold_m=0,
    )

    await coordinator.async_refresh()
    current_location = moved
    await coordinator.async_refresh()

    assert [call[:2] for call in provider.station_calls] == [initial, moved]


@pytest.mark.asyncio
async def test_invalid_tracked_location_preserves_data_and_miss_count_then_recovers(
    hass, sample_stations
):
    station = sample_stations[0]
    initial = (52.0, 5.0)
    moved = (52.0011, 5.0)
    current_location = initial
    provider = FakeProvider([(station,), (), (), ()])
    coordinator = _coordinator(
        hass,
        provider,
        location_resolver=lambda: current_location,
        location_movement_threshold_m=100,
    )

    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert coordinator._missing_refreshes == {station.provider_id: 1}
    cached_data = dict(coordinator.data)
    last_successful_update = coordinator.last_successful_update

    current_location = None
    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert len(provider.station_calls) == 2
    assert coordinator.data == cached_data
    assert coordinator._missing_refreshes == {station.provider_id: 1}
    assert coordinator.last_successful_update == last_successful_update
    assert coordinator.removed_station_ids == set()

    current_location = moved
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert len(provider.station_calls) == 3
    assert provider.station_calls[-1][:2] == moved
    assert coordinator._missing_refreshes == {station.provider_id: 2}
    assert station.provider_id not in coordinator.removed_station_ids

    await coordinator.async_refresh()
    assert station.provider_id not in coordinator.data
    assert coordinator.removed_station_ids == {station.provider_id}


@pytest.mark.asyncio
async def test_provider_failure_preserves_favourite_station_data(hass, sample_stations):
    station = sample_stations[0]
    provider = FakeProvider([(station,), ProviderConnectionError("offline")])
    coordinator = _coordinator(hass, provider, favourites=(_favourite(station),))
    await coordinator.async_refresh()
    previous = coordinator.data.copy()

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert coordinator.data == previous
    assert station.provider_id not in coordinator.removed_station_ids


@pytest.mark.asyncio
async def test_rate_limit_retry_after_extends_update_interval(hass):
    provider = FakeProvider([ProviderRateLimitError("limited", retry_after=600)])
    coordinator = _coordinator(hass, provider)

    with pytest.raises(UpdateFailed) as error:
        await coordinator._async_update_data()

    assert coordinator.update_interval >= timedelta(seconds=600)
    assert error.value.retry_after == 600
    assert error.value.translation_domain == DOMAIN
    assert error.value.translation_key == "rate_limited"


@pytest.mark.asyncio
async def test_authentication_failure_does_not_log_provider_message(hass, caplog):
    token = "private-auth-token"
    coordinator = _coordinator(hass, FakeProvider([AuthenticationError(token)]))

    with caplog.at_level(logging.WARNING):
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    assert token not in caplog.text
    assert "CurbCharge data update failed (AuthenticationError)" in caplog.text


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


@pytest.mark.asyncio
async def test_favourite_is_included_when_unknown_availability_is_disabled(
    hass, sample_stations
):
    nearby, _, unknown = sample_stations
    favourite = replace(unknown, distance_m=1_000)
    provider = FakeProvider([(nearby, favourite)])
    coordinator = _coordinator(
        hass,
        provider,
        include_unknown=False,
        favourites=(_favourite(favourite),),
    )

    await coordinator.async_refresh()

    assert set(coordinator.data) == {nearby.provider_id, favourite.provider_id}
    assert coordinator.nearby_station_ids == {nearby.provider_id}


@pytest.mark.asyncio
async def test_coordinator_rejects_unconfigured_favourite_result(hass, sample_stations):
    station = sample_stations[0]

    class UnexpectedFavouriteProvider(FakeProvider):
        async def async_get_stations(
            self,
            latitude,
            longitude,
            radius_m,
            max_stations,
            favourites=(),
            discovery_filters=None,
            include_unknown=True,
        ):
            return StationDiscoveryResult(nearby=(), favourites=(station,))

    coordinator = _coordinator(hass, UnexpectedFavouriteProvider())

    with pytest.raises(UpdateFailed, match="unexpected favourite"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_missing_saved_favourite_is_placeholder_then_recovers(
    hass, sample_stations
):
    favourite = sample_stations[0]
    provider = FakeProvider([(), (favourite,)])
    coordinator = _coordinator(hass, provider, favourites=(_favourite(favourite),))

    await coordinator.async_refresh()

    placeholder = coordinator.data[favourite.provider_id]
    assert placeholder.is_stale
    assert placeholder.name == favourite.provider_id
    assert placeholder.latitude == favourite.latitude
    assert favourite.provider_id not in coordinator.removed_station_ids

    await coordinator.async_refresh()

    recovered = coordinator.data[favourite.provider_id]
    assert not recovered.is_stale
    assert recovered.available_connectors == favourite.available_connectors


@pytest.mark.asyncio
async def test_favourites_are_retained_but_unfavourited_stations_use_three_misses(
    hass, sample_stations
):
    favourite, ordinary = sample_stations[:2]
    provider = FakeProvider([(favourite, ordinary), (), (), ()])
    coordinator = _coordinator(hass, provider, favourites=(_favourite(favourite),))
    await coordinator.async_refresh()

    for _ in range(2):
        await coordinator.async_refresh()
        assert coordinator.data[favourite.provider_id].is_stale
        assert coordinator.data[ordinary.provider_id].is_stale
        assert ordinary.provider_id not in coordinator.removed_station_ids

    await coordinator.async_refresh()

    assert favourite.provider_id in coordinator.data
    assert coordinator.data[favourite.provider_id].is_stale
    assert ordinary.provider_id not in coordinator.data
    assert coordinator.removed_station_ids == {ordinary.provider_id}


@pytest.mark.asyncio
async def test_unfavouriting_resumes_three_successful_miss_removal(
    hass, sample_stations
):
    favourite = sample_stations[0]
    provider = FakeProvider([(favourite,), (), (), ()])
    coordinator = _coordinator(hass, provider, favourites=(_favourite(favourite),))
    await coordinator.async_refresh()

    coordinator.update_options(
        radius_m=500,
        max_stations=10,
        scan_interval_minutes=5,
        include_unknown=True,
        favourites=(),
    )
    for _ in range(2):
        await coordinator.async_refresh()
        assert coordinator.data[favourite.provider_id].is_stale
        assert favourite.provider_id not in coordinator.removed_station_ids

    await coordinator.async_refresh()

    assert favourite.provider_id not in coordinator.data
    assert coordinator.removed_station_ids == {favourite.provider_id}


@pytest.mark.asyncio
async def test_update_failure_logs_once_until_provider_recovers(
    hass, sample_stations, caplog
):
    provider = FakeProvider(
        [
            ProviderConnectionError("offline"),
            ProviderConnectionError("offline"),
            (sample_stations[0],),
            ProviderConnectionError("offline"),
        ]
    )
    coordinator = _coordinator(hass, provider)

    with caplog.at_level(logging.INFO):
        for _ in range(2):
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()
        await coordinator._async_update_data()
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    assert [
        record.message for record in caplog.records if record.levelno == logging.WARNING
    ] == [
        "CurbCharge data update failed (ProviderConnectionError)",
        "CurbCharge data update failed (ProviderConnectionError)",
    ]
    assert [
        record.message for record in caplog.records if record.levelno == logging.INFO
    ] == ["CurbCharge data updates have recovered"]
