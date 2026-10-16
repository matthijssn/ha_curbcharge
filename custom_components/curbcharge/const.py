"""Constants for the CurbCharge integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "curbcharge"
PLATFORMS = (Platform.BINARY_SENSOR, Platform.SENSOR)

CONF_LOCATION_SOURCE = "location_source"
CONF_LOCATION_ENTITY = "location_entity"
CONF_LOCATION_MOVEMENT_THRESHOLD = "location_movement_threshold"
CONF_PROVIDER = "provider"
CONF_API_TOKEN = "api_token"
CONF_SEARCH_RADIUS = "search_radius"
CONF_MAX_STATIONS = "max_stations"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_INCLUDE_UNKNOWN = "include_unknown"
CONF_CONNECTOR_TYPES = "connector_types"
CONF_MIN_POWER_KW = "min_power_kw"
CONF_REQUIRE_AVAILABLE = "require_available"
CONF_FAVOURITES = "favourites"

LOCATION_HOME = "home"
LOCATION_MANUAL = "manual"
LOCATION_ENTITY = "entity"

PROVIDER_NDW_DOT_NL = "ndw_dot_nl"
COUNTRY_NL = "NL"
PROVIDER_NAME = "NDW DOT-NL"
PROVIDER_ECO_MOVEMENT_BE = "eco_movement_be"
PROVIDER_ECO_MOVEMENT_BE_NAME = "Eco-Movement Belgium"
COUNTRY_BE = "BE"
PROVIDER_API_URL = (
    "https://dotnl.ndw.nu/api/rest/geojson/dynamic-road-status/"
    "charge-point-data/v1/features"
)
TARIFF_FEED_URL = "https://opendata.ndw.nu/charging_point_tariffs_ocpi.json.gz"

DEFAULT_SCAN_INTERVAL_MINUTES = 5
MIN_SCAN_INTERVAL_MINUTES = 1
DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M = 100
DEFAULT_SEARCH_RADIUS_M = 500
MIN_SEARCH_RADIUS_M = 100
MAX_SEARCH_RADIUS_M = 10_000
DEFAULT_MAX_STATIONS = 10
MIN_MAX_STATIONS = 1
MAX_MAX_STATIONS = 50
DEFAULT_INCLUDE_UNKNOWN = True
DEFAULT_CONNECTOR_TYPES: tuple[str, ...] = ()
DEFAULT_MIN_POWER_KW: float | None = None
DEFAULT_REQUIRE_AVAILABLE = False
NDW_CONNECTOR_TYPE_SELECTOR_OPTIONS = (
    ("type-2", "IEC_62196_T2"),
    ("ccs-combo-2", "IEC_62196_T2_COMBO"),
    ("chademo", "CHADEMO"),
    ("cee-16a-single-phase", "IEC_60309_2_SINGLE_16"),
    ("cee-16a-three-phase", "IEC_60309_2_THREE_16"),
    ("cee-32a-three-phase", "IEC_60309_2_THREE_32"),
    ("domestic-a", "DOMESTIC_A"),
)
NDW_CONNECTOR_TYPE_CODES = tuple(
    code for _selector_value, code in NDW_CONNECTOR_TYPE_SELECTOR_OPTIONS
)

MISSING_STATION_REFRESHES = 3
TARIFF_CACHE_TTL = timedelta(hours=6)
GEOJSON_REQUEST_TIMEOUT = timedelta(seconds=20)
TARIFF_REQUEST_TIMEOUT = timedelta(seconds=60)
MAX_BBOX_AREA_DEG2 = 1.0
