"""Tests for CurbCharge sensor values and attributes."""

from dataclasses import replace
from decimal import Decimal

from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.curbcharge.const import DOMAIN, PROVIDER_ECO_MOVEMENT_BE
from custom_components.curbcharge.coordinator import CurbChargeCoordinator
from custom_components.curbcharge.models import (
    ChargingConnector,
    ChargingLocation,
    ChargingTariff,
    ConnectorStatus,
    EVSEStatus,
    FavouriteStationReference,
    TariffStatus,
)
from custom_components.curbcharge.sensor import (
    AvailableConnectorsSensor,
    BestNearbyChargerSensor,
    ConnectorGroupSensor,
    DistanceSensor,
    MaximumChargingPowerSensor,
    NearbyAvailabilityPercentageSensor,
    NearbyAvailableConnectorsSensor,
    NearbyChargingUnitsSensor,
    OccupiedConnectorsSensor,
    PricePerKwhSensor,
    StatusSummarySensor,
    TotalConnectorsSensor,
    UnavailableConnectorsSensor,
)
from tests.conftest import FakeProvider


def _coordinator(
    hass,
    station,
    *,
    max_stations=10,
    include_unknown=True,
    provider=None,
    favourites=(),
):
    if provider is None:
        provider = FakeProvider()
    coordinator = CurbChargeCoordinator(
        hass,
        provider,
        52.0,
        5.0,
        500,
        max_stations,
        5,
        include_unknown,
        favourites,
    )
    coordinator.async_set_updated_data({station.provider_id: station})
    return coordinator


def _belgian_evse(
    station, location, evse_id, connector_count, available_count, *, count_reported=True
):
    connector = ChargingConnector(
        provider_connector_id=f"{evse_id}:connector-group",
        connector_type="TYPE2",
        status=ConnectorStatus.UNKNOWN,
        max_power_kw=22.0,
        count=connector_count,
        available_count=available_count,
    )
    return replace(
        station,
        provider_id=evse_id,
        name=evse_id,
        connectors=(connector,),
        provider=PROVIDER_ECO_MOVEMENT_BE,
        provider_name="Eco-Movement Belgium",
        country_code="BE",
        location=location,
        evse_id=evse_id,
        evse_status=EVSEStatus.AVAILABLE,
        availability_reported=True,
        connector_count_reported=count_reported,
    )


def _favourite_reference(station):
    return FavouriteStationReference(
        provider=station.provider,
        provider_id=station.provider_id,
        name=station.name,
        latitude=station.latitude,
        longitude=station.longitude,
    )


def test_nearby_charging_units_counts_current_records_and_reports_scope(
    hass, sample_stations
):
    current, stale, unknown_availability = sample_stations
    coordinator = _coordinator(hass, current, max_stations=2)
    coordinator.async_set_updated_data(
        {
            current.provider_id: current,
            stale.provider_id: replace(stale, is_stale=True),
            unknown_availability.provider_id: unknown_availability,
        }
    )
    sensor = NearbyChargingUnitsSensor(
        coordinator,
        MockConfigEntry(domain=DOMAIN, unique_id="test-nearby-area"),
    )

    assert sensor.native_value == 2
    assert sensor.unique_id == "test-nearby-area_nearby_charging_units"
    assert sensor.state_class is SensorStateClass.MEASUREMENT
    assert sensor.extra_state_attributes == {
        "provider": "NDW DOT-NL",
        "provider_id": "ndw_dot_nl",
        "country_code": "NL",
        "supported_country_codes": ["NL", "BE"],
        "coverage_scope": "configured_search_area",
        "search_radius_m": 500,
        "max_stations": 2,
        "counted_unit": "charging_station",
        "include_unknown": True,
        "unknown_availability_count": 1,
        "stale_station_count": 1,
        "results_may_be_truncated": True,
        "last_successful_update": None,
    }


def test_nearby_charging_units_handles_uninitialized_and_empty_data(
    hass, sample_stations
):
    provider = FakeProvider([(sample_stations[0],)])
    coordinator = CurbChargeCoordinator(hass, provider, 52.0, 5.0, 500, 2, 5, True)
    sensor = NearbyChargingUnitsSensor(
        coordinator,
        MockConfigEntry(domain=DOMAIN, unique_id="test-nearby-area"),
    )

    assert sensor.native_value is None
    assert sensor.extra_state_attributes["unknown_availability_count"] is None
    assert sensor.extra_state_attributes["results_may_be_truncated"] is None
    assert len(provider.snapshots) == 1

    coordinator.async_set_updated_data({})
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes["unknown_availability_count"] == 0
    assert sensor.extra_state_attributes["stale_station_count"] == 0
    assert sensor.extra_state_attributes["results_may_be_truncated"] is False
    assert len(provider.snapshots) == 1


def test_nearby_charging_units_truncation_is_unknown_with_stale_records(
    hass, sample_stations
):
    current, stale = sample_stations[:2]
    coordinator = _coordinator(hass, current, max_stations=2)
    coordinator.async_set_updated_data(
        {
            current.provider_id: current,
            stale.provider_id: replace(stale, is_stale=True),
        }
    )
    sensor = NearbyChargingUnitsSensor(
        coordinator,
        MockConfigEntry(domain=DOMAIN, unique_id="test-nearby-area"),
    )

    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["stale_station_count"] == 1
    assert sensor.extra_state_attributes["results_may_be_truncated"] is None


def test_nearby_charging_units_does_not_claim_truncation_after_filtering(
    hass, sample_stations
):
    coordinator = _coordinator(
        hass, sample_stations[0], max_stations=2, include_unknown=False
    )
    sensor = NearbyChargingUnitsSensor(
        coordinator,
        MockConfigEntry(domain=DOMAIN, unique_id="test-nearby-area"),
    )

    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["results_may_be_truncated"] is None


def test_nearby_available_connectors_sums_nl_groups_and_reports_coverage(
    hass, sample_stations
):
    first, zero, unknown = sample_stations
    stale = replace(first, provider_id="NL-STALE", is_stale=True)
    coordinator = _coordinator(hass, first, max_stations=3)
    coordinator.async_set_updated_data(
        {
            first.provider_id: first,
            zero.provider_id: zero,
            unknown.provider_id: unknown,
            stale.provider_id: stale,
        }
    )
    sensor = NearbyAvailableConnectorsSensor(
        coordinator,
        MockConfigEntry(domain=DOMAIN, unique_id="nl-search-area"),
    )

    assert sensor.native_value == 2
    assert sensor.unique_id == "nl-search-area_nearby_available_connectors"
    assert sensor.state_class is SensorStateClass.MEASUREMENT
    attributes = sensor.extra_state_attributes
    assert attributes["provider_id"] == "ndw_dot_nl"
    assert attributes["country_code"] == "NL"
    assert attributes["counted_unit"] == "connector"
    assert attributes["fresh_station_count"] == 3
    assert attributes["known_availability_station_count"] == 2
    assert attributes["unknown_availability_station_count"] == 1
    assert attributes["stale_station_count"] == 1
    assert attributes["max_stations"] == 3
    assert attributes["results_may_be_truncated"] is True


def test_nearby_available_connectors_returns_unknown_without_fresh_counts(
    hass, sample_stations
):
    unknown = sample_stations[2]
    provider = FakeProvider([(sample_stations[0],)])
    coordinator = CurbChargeCoordinator(hass, provider, 52.0, 5.0, 500, 10, 5, True)
    sensor = NearbyAvailableConnectorsSensor(
        coordinator,
        MockConfigEntry(domain=DOMAIN, unique_id="test-nearby-area"),
    )

    assert sensor.native_value is None
    assert sensor.extra_state_attributes["fresh_station_count"] is None
    assert sensor.extra_state_attributes["unknown_availability_station_count"] is None
    assert len(provider.snapshots) == 1

    coordinator.async_set_updated_data({})
    assert sensor.native_value is None
    assert sensor.available
    assert sensor.extra_state_attributes["fresh_station_count"] == 0
    assert sensor.extra_state_attributes["known_availability_station_count"] == 0
    assert sensor.extra_state_attributes["unknown_availability_station_count"] == 0

    coordinator.async_set_updated_data({unknown.provider_id: unknown})
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["unknown_availability_station_count"] == 1

    coordinator.async_set_updated_data(
        {unknown.provider_id: replace(unknown, is_stale=True)}
    )
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["fresh_station_count"] == 0
    assert sensor.extra_state_attributes["unknown_availability_station_count"] == 0
    assert sensor.extra_state_attributes["stale_station_count"] == 1
    assert len(provider.snapshots) == 1


def test_nearby_available_connectors_distinguishes_known_zero(hass, sample_stations):
    station = sample_stations[1]
    coordinator = _coordinator(hass, station)
    sensor = NearbyAvailableConnectorsSensor(
        coordinator,
        MockConfigEntry(domain=DOMAIN, unique_id="test-nearby-area"),
    )

    assert sensor.native_value == 0
    assert sensor.extra_state_attributes["known_availability_station_count"] == 1
    assert sensor.extra_state_attributes["unknown_availability_station_count"] == 0


def test_nearby_available_connectors_sums_be_evse_counts_once_per_unit(
    hass, sample_stations
):
    location = ChargingLocation(
        provider_id="site-1",
        provider=PROVIDER_ECO_MOVEMENT_BE,
        provider_name="Eco-Movement Belgium",
        country_code="BE",
        name="Example site",
        latitude=50.85,
        longitude=4.35,
        address=None,
        operator="Example CPO",
        last_updated=None,
    )
    first = _belgian_evse(sample_stations[0], location, "evse-1", 2, 1)
    second = _belgian_evse(sample_stations[0], location, "evse-2", 3, 2)
    provider = FakeProvider()
    provider.provider_id = PROVIDER_ECO_MOVEMENT_BE
    provider.provider_name = "Eco-Movement Belgium"
    provider.country_code = "BE"
    coordinator = _coordinator(hass, first, provider=provider)
    coordinator.async_set_updated_data(
        {first.provider_id: first, second.provider_id: second}
    )
    sensor = NearbyAvailableConnectorsSensor(
        coordinator,
        MockConfigEntry(domain=DOMAIN, unique_id="be-search-area"),
    )

    assert first.location is second.location
    assert sensor.native_value == 3
    assert sensor.extra_state_attributes["provider_id"] == PROVIDER_ECO_MOVEMENT_BE
    assert sensor.extra_state_attributes["country_code"] == "BE"
    assert sensor.extra_state_attributes["fresh_station_count"] == 2
    assert sensor.extra_state_attributes["known_availability_station_count"] == 2
    assert sensor.extra_state_attributes["unknown_availability_station_count"] == 0


def test_nearby_available_connectors_does_not_infer_from_be_evse_status(
    hass, sample_stations
):
    location = ChargingLocation(
        provider_id="site-1",
        provider=PROVIDER_ECO_MOVEMENT_BE,
        provider_name="Eco-Movement Belgium",
        country_code="BE",
        name="Example site",
        latitude=50.85,
        longitude=4.35,
        address=None,
        operator="Example CPO",
        last_updated=None,
    )
    station = _belgian_evse(
        sample_stations[0],
        location,
        "evse-unknown-count",
        2,
        None,
        count_reported=False,
    )
    provider = FakeProvider()
    provider.provider_id = PROVIDER_ECO_MOVEMENT_BE
    provider.provider_name = "Eco-Movement Belgium"
    provider.country_code = "BE"
    coordinator = _coordinator(hass, station, provider=provider)
    sensor = NearbyAvailableConnectorsSensor(
        coordinator,
        MockConfigEntry(domain=DOMAIN, unique_id="be-search-area"),
    )

    assert station.has_available_connectors is True
    assert station.available_connectors is None
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["unknown_availability_station_count"] == 1


def test_nearby_availability_percentage_sums_known_groups_and_reports_coverage(
    hass, sample_stations
):
    first, zero, _unknown = sample_stations
    partial = replace(
        first,
        provider_id="NL-PARTIAL",
        connectors=(
            ChargingConnector(
                "known-group",
                None,
                ConnectorStatus.UNKNOWN,
                None,
                count=3,
                available_count=1,
            ),
            ChargingConnector(
                "unknown-group",
                None,
                ConnectorStatus.UNKNOWN,
                None,
                count=2,
            ),
        ),
    )
    stale = replace(first, provider_id="NL-STALE", is_stale=True)
    coordinator = _coordinator(hass, first, max_stations=3)
    coordinator.async_set_updated_data(
        {
            first.provider_id: first,
            zero.provider_id: zero,
            partial.provider_id: partial,
            stale.provider_id: stale,
        }
    )
    sensor = NearbyAvailabilityPercentageSensor(
        coordinator,
        MockConfigEntry(domain=DOMAIN, unique_id="nl-search-area"),
    )

    assert partial.available_connectors is None
    assert sensor.native_value == 33.3
    assert sensor.native_unit_of_measurement == PERCENTAGE
    assert sensor.state_class is SensorStateClass.MEASUREMENT
    assert sensor.unique_id == "nl-search-area_nearby_availability_percentage"
    attributes = sensor.extra_state_attributes
    assert attributes["available_connectors"] == 3
    assert attributes["total_connectors"] == 9
    assert attributes["reported_total_connectors"] == 11
    assert attributes["availability_coverage_percent"] == 81.8
    assert attributes["unknown_availability_connector_count"] == 2
    assert attributes["known_connector_group_count"] == 4
    assert attributes["excluded_connector_group_count"] == 1
    assert attributes["unknown_availability_station_count"] == 1
    assert attributes["unreported_total_station_count"] == 0
    assert attributes["fresh_station_count"] == 3
    assert attributes["stale_station_count"] == 1


def test_nearby_availability_percentage_reports_full_and_zero(hass, sample_stations):
    full = sample_stations[0]
    full = replace(
        full,
        connectors=tuple(
            replace(
                connector,
                status=ConnectorStatus.AVAILABLE,
                available_count=None,
            )
            for connector in full.connectors
        ),
    )
    full_sensor = NearbyAvailabilityPercentageSensor(
        _coordinator(hass, full),
        MockConfigEntry(domain=DOMAIN, unique_id="full-area"),
    )
    zero_sensor = NearbyAvailabilityPercentageSensor(
        _coordinator(hass, sample_stations[1]),
        MockConfigEntry(domain=DOMAIN, unique_id="zero-area"),
    )

    assert full_sensor.native_value == 100.0
    assert zero_sensor.native_value == 0.0


def test_nearby_availability_percentage_is_unknown_without_a_denominator(
    hass, sample_stations
):
    provider = FakeProvider([(sample_stations[0],)])
    coordinator = CurbChargeCoordinator(hass, provider, 52.0, 5.0, 500, 10, 5, True)
    sensor = NearbyAvailabilityPercentageSensor(
        coordinator,
        MockConfigEntry(domain=DOMAIN, unique_id="test-nearby-area"),
    )

    assert sensor.native_value is None
    assert sensor.extra_state_attributes["total_connectors"] is None
    coordinator.async_set_updated_data({})
    assert sensor.native_value is None
    assert sensor.available
    assert sensor.extra_state_attributes["total_connectors"] == 0
    assert sensor.extra_state_attributes["availability_coverage_percent"] is None

    unknown = replace(
        sample_stations[0],
        connectors=(
            ChargingConnector(
                "unknown-group",
                None,
                ConnectorStatus.UNKNOWN,
                None,
                count=2,
            ),
        ),
    )
    coordinator.async_set_updated_data({unknown.provider_id: unknown})
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["reported_total_connectors"] == 2
    assert sensor.extra_state_attributes["unknown_availability_connector_count"] == 2
    assert sensor.extra_state_attributes["availability_coverage_percent"] == 0.0

    unreported_availability = replace(unknown, availability_reported=False)
    coordinator.async_set_updated_data(
        {unreported_availability.provider_id: unreported_availability}
    )
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["reported_total_connectors"] == 2
    assert sensor.extra_state_attributes["unknown_availability_connector_count"] == 2
    assert sensor.extra_state_attributes["unknown_availability_station_count"] == 1

    unreported_total = replace(unknown, connector_count_reported=False)
    coordinator.async_set_updated_data({unreported_total.provider_id: unreported_total})
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["unreported_total_station_count"] == 1
    assert sensor.extra_state_attributes["total_connectors"] == 0

    coordinator.async_set_updated_data(
        {unknown.provider_id: replace(unknown, is_stale=True)}
    )
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["fresh_station_count"] == 0
    assert sensor.extra_state_attributes["stale_station_count"] == 1
    assert len(provider.snapshots) == 1


def test_nearby_availability_percentage_does_not_infer_be_evse_status(
    hass, sample_stations
):
    location = ChargingLocation(
        provider_id="site-1",
        provider=PROVIDER_ECO_MOVEMENT_BE,
        provider_name="Eco-Movement Belgium",
        country_code="BE",
        name="Example site",
        latitude=50.85,
        longitude=4.35,
        address=None,
        operator="Example CPO",
        last_updated=None,
    )
    station = _belgian_evse(sample_stations[0], location, "evse-available", 2, None)
    provider = FakeProvider()
    provider.provider_id = PROVIDER_ECO_MOVEMENT_BE
    provider.provider_name = "Eco-Movement Belgium"
    provider.country_code = "BE"
    sensor = NearbyAvailabilityPercentageSensor(
        _coordinator(hass, station, provider=provider),
        MockConfigEntry(domain=DOMAIN, unique_id="be-search-area"),
    )

    assert station.has_available_connectors is True
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["reported_total_connectors"] == 2
    assert sensor.extra_state_attributes["unknown_availability_connector_count"] == 2
    assert sensor.extra_state_attributes["availability_coverage_percent"] == 0.0


def test_connector_sensors_do_not_infer_occupancy(hass, sample_stations):
    station = sample_stations[0]
    coordinator = _coordinator(hass, station)

    available = AvailableConnectorsSensor(coordinator, station)
    assert available.native_value == 2
    assert available.entity_category is EntityCategory.DIAGNOSTIC
    assert UnavailableConnectorsSensor(coordinator, station).native_value == 2
    assert OccupiedConnectorsSensor(coordinator, station).native_value is None
    assert TotalConnectorsSensor(coordinator, station).native_value == 4
    assert MaximumChargingPowerSensor(coordinator, station).native_value == 50
    assert DistanceSensor(coordinator, station).native_value == 0


def test_connector_group_sensor_exposes_provider_group_counts(hass, sample_stations):
    station = sample_stations[0]
    coordinator = _coordinator(hass, station)
    connector = station.connectors[0]
    sensor = ConnectorGroupSensor(coordinator, station, connector)

    assert sensor.native_value == connector.count
    assert sensor.extra_state_attributes["available_connectors"] == (
        connector.available_count
    )
    assert sensor.extra_state_attributes["connector_type"] == connector.connector_type
    assert sensor.entity_registry_enabled_default is False

    unknown_connector = replace(connector, connector_type=None)
    unknown_sensor = ConnectorGroupSensor(coordinator, station, unknown_connector)
    assert unknown_sensor.translation_placeholders["connector_type"] == "Unknown"


def test_price_sensor_uses_decimal_tariff_and_currency_unit(hass, sample_stations):
    tariff = ChargingTariff(
        "EUR",
        Decimal("0.257"),
        None,
        None,
        "Public tariff",
        "NDW DOT-NL",
        tariff_id="tariff-1",
        tariff_type="AD_HOC_PAYMENT",
        status=TariffStatus.AVAILABLE,
    )
    station = replace(
        sample_stations[0],
        connectors=(),
        tariff=tariff,
        tariff_status=TariffStatus.AVAILABLE,
    )
    coordinator = _coordinator(hass, station)
    sensor = PricePerKwhSensor(coordinator, station)

    assert sensor.native_value == 0.257
    assert sensor.native_unit_of_measurement == "EUR/kWh"
    attributes = sensor.extra_state_attributes
    assert attributes["price_per_kwh"] == 0.257
    assert attributes["currency"] == "EUR"
    assert attributes["tariff_description"] == "Public tariff"
    assert attributes["tariff_source"] == "NDW DOT-NL"
    assert attributes["tariff_ids"] == ["tariff-1"]
    assert attributes["tariff_type"] == "AD_HOC_PAYMENT"
    assert attributes["tariff_status"] == "available"

    summary_attributes = StatusSummarySensor(
        coordinator, station
    ).extra_state_attributes
    assert {key: summary_attributes[key] for key in attributes} == attributes


def test_price_sensor_is_unknown_without_tariff(hass, sample_stations):
    station = sample_stations[0]
    sensor = PricePerKwhSensor(_coordinator(hass, station), station)

    assert sensor.native_value is None
    assert sensor.native_unit_of_measurement is None

    unknown_price = ChargingTariff("EUR", None, None, None, None, "NDW DOT-NL")
    station = replace(station, tariff=unknown_price)
    sensor = PricePerKwhSensor(_coordinator(hass, station), station)
    assert sensor.native_value is None
    assert sensor.native_unit_of_measurement == "EUR/kWh"
    assert sensor.extra_state_attributes["tariff_status"] == "incomplete"


def test_status_summary_exposes_operational_and_provider_status(hass, sample_stations):
    station = sample_stations[0]
    coordinator = _coordinator(hass, station)
    sensor = StatusSummarySensor(coordinator, station)

    assert sensor.native_value == "available"
    assert sensor.extra_state_attributes["available_connectors"] == 2
    assert sensor.extra_state_attributes["occupied_connectors"] is None
    assert sensor.extra_state_attributes["unknown_connectors"] == 2
    assert sensor.extra_state_attributes["reserved_connectors"] is None
    assert sensor.extra_state_attributes["is_open"] is True
    assert sensor.extra_state_attributes["latitude"] == 52.0
    assert sensor.extra_state_attributes["address"] == "Teststraat 1"
    assert sensor.extra_state_attributes["station_name"] == station.name
    assert sensor.extra_state_attributes["total_connectors"] == station.total_connectors
    assert sensor.extra_state_attributes["distance_m"] == station.distance_m
    assert sensor.extra_state_attributes["max_power_kw"] == station.max_power_kw
    assert sensor.extra_state_attributes["tariff_status"] == "incomplete"
    tariff_groups = [
        group
        for group in sensor.extra_state_attributes["connector_tariffs"]
        if group["tariff_ids"]
    ]
    assert len(tariff_groups) == 1
    assert tariff_groups[0]["tariff_ids"] == ["tariff-eur-022"]


def test_summary_exposes_conflicting_connector_tariffs(hass, sample_stations):
    station = sample_stations[0]
    tariff_a = ChargingTariff("EUR", Decimal("0.2"), None, None, None, "NDW DOT-NL")
    tariff_b = ChargingTariff("EUR", Decimal("0.3"), None, None, None, "NDW DOT-NL")
    connector_a = replace(
        station.connectors[0],
        tariff_ids=("tariff-a",),
        tariffs=(tariff_a,),
        tariff_status=TariffStatus.AVAILABLE,
    )
    connector_b = replace(
        station.connectors[1],
        tariff_ids=("tariff-b",),
        tariffs=(tariff_b,),
        tariff_status=TariffStatus.AVAILABLE,
    )
    station = replace(
        station,
        connectors=(connector_a, connector_b),
        tariff=None,
        tariff_status=TariffStatus.AMBIGUOUS,
    )
    coordinator = _coordinator(hass, station)

    summary_attributes = StatusSummarySensor(
        coordinator, station
    ).extra_state_attributes
    price_attributes = PricePerKwhSensor(coordinator, station).extra_state_attributes

    assert summary_attributes["price_per_kwh"] is None
    assert summary_attributes["tariff_status"] == "ambiguous"
    assert [
        group["tariffs"][0]["price_per_kwh"]
        for group in summary_attributes["connector_tariffs"]
    ] == [0.2, 0.3]
    assert price_attributes["price_per_kwh"] is None
    assert price_attributes["tariff_status"] == "ambiguous"


def test_best_nearby_sensor_exposes_selection_reason(hass, sample_stations):
    station = sample_stations[0]
    coordinator = _coordinator(hass, station)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="hashed-area")
    sensor = BestNearbyChargerSensor(coordinator, entry)

    assert sensor.native_value == station.name
    assert sensor.extra_state_attributes["station_id"] == station.provider_id
    assert sensor.extra_state_attributes["available_connectors"] == 2
    assert sensor.extra_state_attributes["reason"] == "only_available_station"


def test_status_summary_exposes_favourite_per_config_entry(hass, sample_stations):
    station = sample_stations[0]
    favourite_coordinator = _coordinator(
        hass, station, favourites=(_favourite_reference(station),)
    )
    regular_coordinator = _coordinator(hass, station)

    assert StatusSummarySensor(favourite_coordinator, station).extra_state_attributes[
        "is_favorite"
    ]
    assert not StatusSummarySensor(regular_coordinator, station).extra_state_attributes[
        "is_favorite"
    ]


def test_nearby_aggregates_and_best_exclude_remote_favourites(hass, sample_stations):
    normal = replace(sample_stations[0], provider_id="normal-available", distance_m=100)
    nearby_favourite = replace(
        sample_stations[0], provider_id="nearby-favourite", distance_m=400
    )
    remote_favourite = replace(
        sample_stations[0],
        provider_id="remote-favourite",
        distance_m=2_000,
        tariff=ChargingTariff("EUR", Decimal("0.01"), None, None, None, "provider"),
    )
    favourites = (
        _favourite_reference(nearby_favourite),
        _favourite_reference(remote_favourite),
    )
    coordinator = _coordinator(hass, normal, max_stations=1, favourites=favourites)
    coordinator.async_set_updated_data(
        {
            normal.provider_id: normal,
            nearby_favourite.provider_id: nearby_favourite,
            remote_favourite.provider_id: remote_favourite,
        }
    )
    coordinator.nearby_station_ids = {normal.provider_id}

    area_entry = MockConfigEntry(domain=DOMAIN, unique_id="favorite-area")
    units = NearbyChargingUnitsSensor(coordinator, area_entry)
    available = NearbyAvailableConnectorsSensor(coordinator, area_entry)
    best = BestNearbyChargerSensor(coordinator, area_entry)

    assert units.native_value == 2
    assert available.native_value == 4
    assert best.native_value == normal.name


def test_entities_are_unavailable_without_current_station_data(hass, sample_stations):
    station = sample_stations[0]
    stale_coordinator = _coordinator(hass, replace(station, is_stale=True))
    assert not AvailableConnectorsSensor(stale_coordinator, station).available

    coordinator = _coordinator(hass, station)
    connector_sensor = AvailableConnectorsSensor(coordinator, station)
    assert connector_sensor.available

    coordinator.async_set_updated_data({})
    assert not connector_sensor.available
    assert StatusSummarySensor(coordinator, station).native_value == "unknown"
    assert StatusSummarySensor(coordinator, station).extra_state_attributes == {
        "provider_station_id": station.provider_id
    }

    entry = MockConfigEntry(domain=DOMAIN, unique_id="hashed-area")
    best_sensor = BestNearbyChargerSensor(coordinator, entry)
    assert best_sensor.native_value is None
    assert best_sensor.extra_state_attributes["reason"] == "no_available_station"
