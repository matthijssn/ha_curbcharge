"""Tests for typed station and connector availability models."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from custom_components.curbcharge.const import (
    CONF_ALLOW_FALLBACK,
    CONF_CONNECTOR_TYPE_IDS,
    CONF_EXCLUDED_OPERATORS,
    CONF_MAXIMUM_PRICE,
    CONF_MAXIMUM_PRICE_CURRENCY,
    CONF_PERSONALIZATION_ENABLED,
    CONF_PERSONALIZATION_MIN_POWER_KW,
    CONF_PERSONALIZATION_VERSION,
    CONF_PREFERRED_OPERATORS,
    CONF_RANKING_PRESET,
    CONF_UNKNOWN_DATA_POLICY,
    CONFIG_ENTRY_VERSION,
    NDW_CONNECTOR_TYPE_CODES,
    PERSONALIZATION_PROFILE_VERSION,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_NDW_DOT_NL,
    PROVIDER_NOBIL_NO,
)
from custom_components.curbcharge.models import (
    ChargingConnector,
    ChargingLocation,
    ConnectorStatus,
    ConnectorTypeId,
    DiscoveryFilterSettings,
    EVSEStatus,
    FavouriteStationReference,
    OperatorReference,
    PersonalizationProfile,
    PersonalizationRankingPreset,
    PersonalizationUnknownDataPolicy,
    ProviderTimestampSource,
    parse_favourite_references,
    parse_operator_reference_lines,
)


def test_canonical_connector_ids_cover_ndw_selector_codes():
    assert {connector_type.value for connector_type in ConnectorTypeId} == set(
        NDW_CONNECTOR_TYPE_CODES
    )


def _personalization_config(**overrides):
    config = {
        CONF_PERSONALIZATION_VERSION: PERSONALIZATION_PROFILE_VERSION,
        CONF_PERSONALIZATION_ENABLED: False,
        CONF_CONNECTOR_TYPE_IDS: [],
        CONF_PERSONALIZATION_MIN_POWER_KW: None,
        CONF_PREFERRED_OPERATORS: [],
        CONF_EXCLUDED_OPERATORS: [],
        CONF_MAXIMUM_PRICE: None,
        CONF_MAXIMUM_PRICE_CURRENCY: None,
        CONF_RANKING_PRESET: PersonalizationRankingPreset.BALANCED.value,
        CONF_ALLOW_FALLBACK: True,
        CONF_UNKNOWN_DATA_POLICY: PersonalizationUnknownDataPolicy.EXCLUDE.value,
    }
    config.update(overrides)
    return config


def test_missing_personalization_profile_defaults_disabled_without_entry_migration():
    profile = PersonalizationProfile.from_config(None)

    assert CONFIG_ENTRY_VERSION == 2
    assert not profile.enabled
    assert profile.as_config() == _personalization_config()


def test_personalization_profile_round_trips_and_deduplicates_values():
    config = _personalization_config(
        **{
            CONF_PERSONALIZATION_ENABLED: True,
            CONF_CONNECTOR_TYPE_IDS: [
                ConnectorTypeId.TYPE_2.value,
                ConnectorTypeId.TYPE_2.value,
            ],
            CONF_PERSONALIZATION_MIN_POWER_KW: 0,
            CONF_PREFERRED_OPERATORS: [
                {"provider": PROVIDER_NDW_DOT_NL, "key": "NLX"},
                {"provider": PROVIDER_ECO_MOVEMENT_BE, "key": "Example   CPO"},
                {"provider": PROVIDER_ECO_MOVEMENT_BE, "key": " EXAMPLE CPO "},
                {"provider": PROVIDER_NOBIL_NO, "key": "Synthetic CPO"},
            ],
            CONF_EXCLUDED_OPERATORS: [
                {"provider": PROVIDER_NDW_DOT_NL, "key": "OTHER"}
            ],
            CONF_MAXIMUM_PRICE: "0.012300",
            CONF_MAXIMUM_PRICE_CURRENCY: "eur",
            CONF_RANKING_PRESET: PersonalizationRankingPreset.HIGHEST_POWER.value,
            CONF_ALLOW_FALLBACK: False,
            CONF_UNKNOWN_DATA_POLICY: (
                PersonalizationUnknownDataPolicy.ALLOW_FALLBACK.value
            ),
        }
    )

    profile = PersonalizationProfile.from_config(config)

    assert profile.enabled
    assert profile.connector_type_ids == (ConnectorTypeId.TYPE_2,)
    assert profile.minimum_power_kw == 0
    assert profile.preferred_operators == (
        OperatorReference(PROVIDER_NDW_DOT_NL, "NLX"),
        OperatorReference(PROVIDER_ECO_MOVEMENT_BE, "example cpo"),
        OperatorReference(PROVIDER_NOBIL_NO, "Synthetic CPO"),
    )
    assert profile.maximum_price == Decimal("0.012300")
    assert profile.maximum_price_currency == "EUR"
    assert profile.ranking_preset is PersonalizationRankingPreset.HIGHEST_POWER
    assert not profile.allow_fallback
    assert (
        profile.unknown_data_policy is PersonalizationUnknownDataPolicy.ALLOW_FALLBACK
    )
    assert profile.as_config()[CONF_MAXIMUM_PRICE] == "0.012300"
    assert PersonalizationProfile.from_config(profile.as_config()) == profile


def test_operator_reference_lines_use_provider_scoped_exact_keys():
    assert parse_operator_reference_lines(
        "\nndw_dot_nl:NLX\n\neco_movement_be: Example   CPO \n"
        "eco_movement_be:example cpo\nnobil_no:Synthetic CPO\n"
    ) == (
        OperatorReference(PROVIDER_NDW_DOT_NL, "NLX"),
        OperatorReference(PROVIDER_ECO_MOVEMENT_BE, "example cpo"),
        OperatorReference(PROVIDER_NOBIL_NO, "Synthetic CPO"),
    )


def test_operator_reference_rejects_non_string_keys_and_formats_input():
    with pytest.raises(ValueError):
        OperatorReference(PROVIDER_NDW_DOT_NL, None)

    assert OperatorReference(PROVIDER_NDW_DOT_NL, "NLX").as_input() == (
        "ndw_dot_nl:NLX"
    )


@pytest.mark.parametrize(
    "value",
    (
        1,
        {"provider": PROVIDER_NDW_DOT_NL, "key": "NLX", "extra": True},
        {"provider": PROVIDER_NDW_DOT_NL, "key": None},
    ),
)
def test_operator_reference_from_config_rejects_invalid_mappings(value):
    with pytest.raises(ValueError):
        OperatorReference.from_config(value)


@pytest.mark.parametrize("value", (False, []))
def test_personalization_profile_rejects_non_mapping(value):
    with pytest.raises(ValueError):
        PersonalizationProfile.from_config(value)


@pytest.mark.parametrize(
    "value",
    (
        None,
        1,
        "not-a-provider-reference",
        "unknown_provider:operator",
        "ndw_dot_nl:",
    ),
)
def test_operator_reference_lines_reject_invalid_values(value):
    with pytest.raises(ValueError):
        parse_operator_reference_lines(value)


@pytest.mark.parametrize(
    "overrides",
    (
        {CONF_PERSONALIZATION_VERSION: 2},
        {CONF_PERSONALIZATION_VERSION: True},
        {CONF_PERSONALIZATION_ENABLED: 1},
        {CONF_CONNECTOR_TYPE_IDS: "IEC_62196_T2"},
        {CONF_CONNECTOR_TYPE_IDS: ["unknown"]},
        {CONF_CONNECTOR_TYPE_IDS: [None]},
        {CONF_PERSONALIZATION_MIN_POWER_KW: True},
        {CONF_PERSONALIZATION_MIN_POWER_KW: -1},
        {CONF_PERSONALIZATION_MIN_POWER_KW: float("inf")},
        {CONF_PERSONALIZATION_MIN_POWER_KW: 10**1000},
        {CONF_PREFERRED_OPERATORS: "not-a-list"},
        {CONF_PREFERRED_OPERATORS: [None]},
        {CONF_PREFERRED_OPERATORS: [{"provider": PROVIDER_NDW_DOT_NL, "key": ""}]},
        {CONF_PREFERRED_OPERATORS: [{"provider": "unknown", "key": "operator"}]},
        {
            CONF_PREFERRED_OPERATORS: [
                {"provider": PROVIDER_NDW_DOT_NL, "key": "NLX", "extra": True}
            ]
        },
        {CONF_PREFERRED_OPERATORS: [{"provider": PROVIDER_NDW_DOT_NL, "key": None}]},
        {
            CONF_PREFERRED_OPERATORS: [
                {"provider": PROVIDER_NDW_DOT_NL, "key": "same"}
            ],
            CONF_EXCLUDED_OPERATORS: [{"provider": PROVIDER_NDW_DOT_NL, "key": "same"}],
        },
        {CONF_MAXIMUM_PRICE: ""},
        {CONF_MAXIMUM_PRICE: 1},
        {CONF_MAXIMUM_PRICE: "not-a-decimal"},
        {CONF_MAXIMUM_PRICE: "NaN"},
        {CONF_MAXIMUM_PRICE: "Infinity"},
        {CONF_MAXIMUM_PRICE: "-0.01"},
        {
            CONF_MAXIMUM_PRICE: "1.00",
            CONF_MAXIMUM_PRICE_CURRENCY: None,
        },
        {CONF_MAXIMUM_PRICE_CURRENCY: "US"},
        {CONF_MAXIMUM_PRICE_CURRENCY: 1},
        {CONF_MAXIMUM_PRICE_CURRENCY: "€UR"},
        {CONF_RANKING_PRESET: "unknown"},
        {CONF_RANKING_PRESET: None},
        {CONF_ALLOW_FALLBACK: 1},
        {CONF_UNKNOWN_DATA_POLICY: "unknown"},
        {CONF_UNKNOWN_DATA_POLICY: None},
        {"unexpected": True},
    ),
)
def test_personalization_profile_rejects_invalid_saved_values(overrides):
    with pytest.raises(ValueError):
        PersonalizationProfile.from_config(_personalization_config(**overrides))


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


def test_provider_data_age_uses_utc_and_returns_zero_at_current_time(sample_stations):
    now = datetime(2025, 1, 2, 3, 6, 5, tzinfo=UTC)
    provider_timezone = timezone(timedelta(hours=5, minutes=30))
    station = replace(
        sample_stations[0],
        last_updated=(now - timedelta(seconds=65)).astimezone(provider_timezone),
        last_updated_source=ProviderTimestampSource.NDW_FEATURE,
    )

    assert (
        station.provider_data_age_seconds(now.astimezone(timezone(timedelta(hours=-7))))
        == 65
    )
    assert (
        replace(
            station,
            last_updated=now.astimezone(provider_timezone),
        ).provider_data_age_seconds(now)
        == 0
    )
    assert (
        replace(
            station,
            last_updated_source=ProviderTimestampSource.NOBIL,
        ).provider_data_age_seconds(now)
        == 65
    )


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
        "location_id": "site-1",
        "location_name": "Mutable site name",
        "location_operator": "Mutable operator",
    }

    assert parse_favourite_references(None, "ndw_dot_nl") == ()
    assert parse_favourite_references([favourite], "ndw_dot_nl") == (
        FavouriteStationReference(
            "ndw_dot_nl", "station-1", "station-1", 52.0, 5.0, "site-1"
        ),
    )
    assert parse_favourite_references([favourite], "ndw_dot_nl")[0].as_config() == {
        "provider": "ndw_dot_nl",
        "provider_id": "station-1",
        "name": "station-1",
        "latitude": 52.0,
        "longitude": 5.0,
        "location_id": "site-1",
    }

    for invalid in (
        "not a list",
        [None],
        [{**favourite, "provider": "eco_movement_be"}],
        [{**favourite, "provider_id": ""}],
        [{**favourite, "provider_id": " "}],
        [{**favourite, "latitude": True}],
        [{**favourite, "latitude": float("nan")}],
        [{**favourite, "longitude": 181}],
        [favourite, favourite],
    ):
        with pytest.raises(ValueError):
            parse_favourite_references(invalid, "ndw_dot_nl")


def test_favourite_identity_is_provider_aware():
    ndw = parse_favourite_references(
        [
            {
                "provider": "ndw_dot_nl",
                "provider_id": "same-id",
                "latitude": 52.0,
                "longitude": 5.0,
            }
        ],
        "ndw_dot_nl",
    )[0]
    belgium = parse_favourite_references(
        [
            {
                "provider": "eco_movement_be",
                "provider_id": "same-id",
                "latitude": 50.8,
                "longitude": 4.3,
            }
        ],
        "eco_movement_be",
    )[0]

    assert (ndw.provider, ndw.provider_id) != (belgium.provider, belgium.provider_id)
    assert ndw.name == ndw.provider_id
    assert belgium.name == belgium.provider_id


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
