"""Tests for typed station and connector availability models."""

from dataclasses import replace

from custom_components.curbcharge.models import (
    ChargingConnector,
    ConnectorStatus,
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
