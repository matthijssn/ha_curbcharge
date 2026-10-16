"""Tests for typed station and connector availability models."""

from dataclasses import replace

import pytest

from custom_components.curbcharge.models import (
    ChargingConnector,
    ChargingLocation,
    ConnectorStatus,
    DiscoveryFilterSettings,
    EVSEStatus,
    FavouriteStationReference,
    parse_favourite_references,
)


def test_station_counts_known_connector_statuses(sample_stations):
    connectors = (
        ChargingConnector(
            "available",
            "CCS",
            ConnectorStatus.AVAILABLE,
            50,
            count=2,
        ),
        ChargingConnector("occupied", "TYPE2", ConnectorStatus.OCCUPIED, 22, count=3),
        ChargingConnector("reserved", "TYPE2", ConnectorStatus.RESERVED, None, count=1),
        ChargingConnector(
            "out-of-service",
            None,
            ConnectorStatus.OUT_OF_SERVICE,
            None,
            count=2,
        ),
    )
    station = replace(sample_stations[0], connectors=connectors)

    assert station.available_connectors == 2
    assert station.total_connectors == 8
    assert station.has_available_connectors is True
    assert station.unavailable_connectors == 6
    assert station.occupied_connectors == 3
    assert station.reserved_connectors == 1
    assert station.out_of_service_connectors == 2
    assert station.unknown_connectors == 0
    assert station.max_power_kw == 50
    assert station.connector_types == ("CCS", "TYPE2")


def test_station_does_not_infer_unknown_connector_statuses(sample_stations):
    connector = ChargingConnector(
        "group", None, ConnectorStatus.UNKNOWN, None, count=3, available_count=1
    )
    station = replace(sample_stations[0], connectors=(connector,))

    assert station.available_connectors == 1
    assert station.total_connectors == 3
    assert station.has_available_connectors is True
    assert station.unavailable_connectors == 2
    assert station.occupied_connectors is None
    assert station.reserved_connectors is None
    assert station.out_of_service_connectors is None
    assert station.unknown_connectors == 2


def test_station_without_availability_remains_unknown(sample_stations):
    station = replace(
        sample_stations[0],
        connectors=(),
        availability_reported=False,
        connector_count_reported=False,
    )

    assert station.available_connectors is None
    assert station.total_connectors is None
    assert station.has_available_connectors is None
    assert station.unavailable_connectors is None
    assert station.occupied_connectors is None
    assert station.reserved_connectors is None
    assert station.out_of_service_connectors is None
    assert station.unknown_connectors is None
    assert station.max_power_kw is None
    assert station.connector_types == ()


def test_discovery_filters_require_one_connector_group_to_match_all_criteria(
    sample_stations,
):
    station = sample_stations[0]
    type_two = next(
        connector
        for connector in station.connectors
        if connector.connector_type == "IEC_62196_T2"
    )
    filters = DiscoveryFilterSettings(
        connector_types=("IEC_62196_T2",),
        minimum_power_kw=50,
        require_available=True,
    )

    assert not filters.matches_station(station)
    assert DiscoveryFilterSettings(
        connector_types=("IEC_62196_T2",),
        minimum_power_kw=22,
        require_available=True,
    ).matches_station(station)

    matching_group = replace(type_two, max_power_kw=50, available_count=1)
    assert filters.matches_station(replace(station, connectors=(matching_group,)))
    assert DiscoveryFilterSettings().matches_station(replace(station, connectors=()))


def test_discovery_filters_reject_unknown_group_data(sample_stations):
    station = sample_stations[0]
    connector = next(
        connector
        for connector in station.connectors
        if connector.connector_type == "IEC_62196_T2"
    )

    assert not DiscoveryFilterSettings(
        connector_types=("IEC_62196_T2",)
    ).matches_station(
        replace(station, connectors=(replace(connector, connector_type=None),))
    )
    assert not DiscoveryFilterSettings(minimum_power_kw=1).matches_station(
        replace(station, connectors=(replace(connector, max_power_kw=None),))
    )
    assert not DiscoveryFilterSettings(require_available=True).matches_station(
        replace(station, connectors=(replace(connector, available_count=None),))
    )
    assert not DiscoveryFilterSettings(require_available=True).matches_station(
        replace(station, connectors=(replace(connector, available_count=0),))
    )


def test_station_with_an_empty_report_has_zero_counts(sample_stations):
    station = replace(sample_stations[0], connectors=())

    assert station.available_connectors == 0
    assert station.total_connectors == 0
    assert station.has_available_connectors is False
    assert station.unavailable_connectors == 0
    assert station.occupied_connectors == 0
    assert station.reserved_connectors == 0
    assert station.out_of_service_connectors == 0
    assert station.unknown_connectors == 0


def test_location_model_is_country_and_provider_independent():
    location = ChargingLocation(
        provider_id="site-123",
        provider="example_provider",
        provider_name="Example Provider",
        country_code="BE",
        name="Example site",
        latitude=50.85,
        longitude=4.35,
        address=None,
        operator="Example CPO",
        last_updated=None,
    )

    assert location.provider_id == "site-123"
    assert location.country_code == "BE"
    assert location.provider == "example_provider"


def test_parse_favourite_references_defaults_empty_and_validates_values():
    favourite = {
        "provider": "ndw_dot_nl",
        "provider_id": "station-1",
        "name": "Example address",
        "latitude": 52.0,
        "longitude": 5.0,
    }

    assert parse_favourite_references(None, "ndw_dot_nl") == ()
    assert parse_favourite_references([favourite], "ndw_dot_nl") == (
        FavouriteStationReference(
            "ndw_dot_nl", "station-1", "Example address", 52.0, 5.0
        ),
    )

    for invalid in (
        "not a list",
        [None],
        [{**favourite, "provider": "eco_movement_be"}],
        [{**favourite, "provider_id": ""}],
        [{**favourite, "name": " "}],
        [{**favourite, "latitude": True}],
        [{**favourite, "latitude": float("nan")}],
        [{**favourite, "longitude": 181}],
        [favourite, favourite],
    ):
        with pytest.raises(ValueError):
            parse_favourite_references(invalid, "ndw_dot_nl")


def test_evse_availability_does_not_invent_connector_counts(sample_stations):
    connector = ChargingConnector(
        "unknown-group", None, ConnectorStatus.UNKNOWN, None, count=1
    )
    station = replace(
        sample_stations[0],
        connectors=(connector,),
        availability_reported=True,
        connector_count_reported=False,
        evse_status=EVSEStatus.AVAILABLE,
    )

    assert station.has_available_connectors is True
    assert station.available_connectors is None
    assert station.total_connectors is None
    assert station.unavailable_connectors is None
    assert station.occupied_connectors is None
    assert station.unknown_connectors is None

    assert (
        replace(station, evse_status=EVSEStatus.CHARGING).has_available_connectors
        is False
    )
    assert (
        replace(station, evse_status=EVSEStatus.UNKNOWN).has_available_connectors
        is None
    )

    no_count = replace(
        station,
        connectors=(),
        evse_status=EVSEStatus.UNKNOWN,
        connector_count_reported=False,
    )
    assert no_count.has_available_connectors is None
