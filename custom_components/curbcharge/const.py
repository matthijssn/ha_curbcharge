"""Constants for the CurbCharge integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "curbcharge"
PLATFORMS = (Platform.BINARY_SENSOR, Platform.SENSOR)

CONF_LOCATION_SOURCE = "location_source"
CONF_PROVIDER = "provider"
CONF_SEARCH_RADIUS = "search_radius"
CONF_MAX_STATIONS = "max_stations"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_INCLUDE_UNKNOWN = "include_unknown"

LOCATION_HOME = "home"
LOCATION_MANUAL = "manual"

PROVIDER_NDW_DOT_NL = "ndw_dot_nl"
PROVIDER_NAME = "NDW DOT-NL"
PROVIDER_API_URL = (
    "https://dotnl.ndw.nu/api/rest/geojson/dynamic-road-status/"
    "charge-point-data/v1/features"
)
TARIFF_FEED_URL = "https://opendata.ndw.nu/charging_point_tariffs_ocpi.json.gz"

DEFAULT_SCAN_INTERVAL_MINUTES = 5
MIN_SCAN_INTERVAL_MINUTES = 1
DEFAULT_SEARCH_RADIUS_M = 500
MIN_SEARCH_RADIUS_M = 100
MAX_SEARCH_RADIUS_M = 10_000
DEFAULT_MAX_STATIONS = 10
MIN_MAX_STATIONS = 1
MAX_MAX_STATIONS = 50
DEFAULT_INCLUDE_UNKNOWN = True

MISSING_STATION_REFRESHES = 3
TARIFF_CACHE_TTL = timedelta(hours=6)
GEOJSON_REQUEST_TIMEOUT = timedelta(seconds=20)
TARIFF_REQUEST_TIMEOUT = timedelta(seconds=60)
MAX_BBOX_AREA_DEG2 = 1.0
