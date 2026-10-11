# CurbCharge

CurbCharge discovers public EV charging points near a Home Assistant home or
manually configured location. It creates one device per charging point and
reports provider-supplied availability, connector counts, power, distance,
operator, tariff data when unambiguous, and a best-nearby recommendation.

## Data source and coverage

The first provider is the Dutch [NDW DOT-NL platform][dotnl], which publishes
data about publicly accessible charging points in the Netherlands. CurbCharge
uses its documented [GeoJSON consumer API][geojson-api] for locations and
availability and its [OCPI tariff data file][tariffs] for tariff components.
Data is provided by charging point operators and made available by NDW on
behalf of the Dutch Ministry of Infrastructure and Water Management. The API
consumer documentation currently warns that its specifications may change.

The GeoJSON endpoint includes a provider `last_updated` value for each point.
Availability is therefore the latest snapshot NDW supplies, not a guaranteed
real-time feed; freshness can differ by operator. The integration exposes the
station's update timestamp and does not invent missing status or price data.
DOT-NL does not supply a station name in this response, so CurbCharge uses its
address as the display name and falls back to the provider station ID if the
address is empty.

DOT-NL reports availability as counts grouped by connector characteristics.
It does not identify individual connectors or distinguish occupied, reserved,
out-of-service, and other unavailable connectors. CurbCharge reports the
available and total counts exactly, derives the count not reported available,
and leaves occupied status unknown when the source cannot establish it. The
`open` field is kept separate as the provider's operational/open status.

Tariffs are shown only when every connector group at a station resolves to the
same active currency and price components. Conditional, conflicting, missing,
or mixed-currency tariffs are treated as unknown. The price sensor remains
unknown rather than zero when no unambiguous energy price is available. OCPI
price components are published before VAT; CurbCharge applies the reported VAT
percentage to present a gross rate and treats a missing VAT rate as unknown.

The GeoJSON OpenAPI description labels `power_max` as kW, while current live
examples contain values such as `15000` for a 15 kW connector. CurbCharge
normalizes values above 1000 as watts; values at or below 1000 are treated as
kW. This follows the observed feed while accommodating the published unit
description.

## Installation

### HACS custom repository

[Add CurbCharge to HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=matthijssn&repository=ha_curbcharge&category=integration),
or:

1. In HACS, open **Integrations** and choose **Custom repositories** from the
   menu.
2. Add the GitHub URL for this repository and select **Integration** as the
   category.
3. Search for **CurbCharge**, install it, and restart Home Assistant.

### Manual

Copy `custom_components/curbcharge` into the Home Assistant configuration
directory at `custom_components/curbcharge`, then restart Home Assistant.

## Configuration

Add **CurbCharge** from **Settings → Devices & services → Add integration**.
Choose either Home Assistant's home coordinates or enter a latitude and
longitude. Home coordinates are read at runtime and are not copied into the
config entry. Manual coordinates are stored locally because they are needed to
make each search.

Choose a search radius from 100 to 10,000 metres, a maximum of 1 to 50 stations
(default 10), and the NDW DOT-NL provider. CurbCharge verifies provider
connectivity and the response format before creating the config entry. The
default polling interval is five minutes; the options flow permits intervals
of one minute or longer, plus changes to the radius, station limit, provider,
and whether to include stations for which no availability data was returned.

## Supported stations

CurbCharge discovers publicly accessible charging points in the Netherlands
that NDW DOT-NL publishes within the configured search area. It reports provider
data; it does not connect to or control physical chargers. Stations outside
the DOT-NL dataset are not discoverable through this integration.

## Entities

Each charging point is one Home Assistant device. CurbCharge creates:

- **Available**: on when the provider reports at least one available connector.
- **Available connectors**, **Occupied connectors**, **Unavailable
  connectors**, and **Total connectors**.
- **Maximum charging power**, **Price per kWh**, and **Distance**.
- **Status summary**, including connector types, operator, address, provider
  status, tariff details, coordinates, and provider update timestamps.
- One integration-level **Best nearby charger** sensor.

For each availability group reported by NDW, the station also has a
disabled-by-default connector-group sensor. The provider groups connectors by type and
power characteristics; these are not individual physical connector IDs.

The occupied sensor is unknown if the provider only reports available and
total counts and some connectors are not available. This avoids treating a
reserved or out-of-service connector as occupied. The unavailable count is
`total - available`, which is the only additional count directly supported by
the NDW availability response.

The recommendation excludes stations with no reported available connectors.
It prefers lower known prices when all candidate prices use the same currency,
then shorter distance, more available connectors, higher power, and a stable
station identifier. It never compares different currencies; its `reason`
attribute is a stable reason code.

When a station is absent from three consecutive successful refreshes, its
entities and device are removed. Failed refreshes do not count as absence and
do not replace the last successful coordinator data.

## Data updates

CurbCharge refreshes station data with a shared `DataUpdateCoordinator`. The
coordinator calls the provider once per cycle; entities never make requests.
The default interval is five minutes and can be reduced to one minute in
options. NDW may paginate GeoJSON results, and tariffs come from a separate
OCPI file cached for six hours. The provider client may therefore make
additional HTTP requests when more pages or uncached tariffs are needed.
NDW's station `last_updated` time is shown separately from Home Assistant's
last successful refresh time; upstream data can be delayed even immediately
after a successful poll.

## Use cases and automations

Use the **Available** binary sensor to notify when a station reports an
available connector, or use **Best nearby charger** as a recommendation input.
Replace the example entity ID with the one created for your station:

```yaml
alias: Notify when a charging station is available
trigger:
  - platform: state
    entity_id: binary_sensor.your_station_available
    to: "on"
action:
  - service: persistent_notification.create
    data:
      title: Charging station available
      message: A connector is reported available at this station.
mode: single
```

## Services

CurbCharge is read-only and registers no custom services, actions, triggers, or
conditions. Use its entities in Home Assistant automations as shown above.

## Troubleshooting

- **No stations appear:** Check the configured home/manual location, radius,
  and that public stations are listed in NDW DOT-NL for that area.
- **Setup cannot connect:** Check Home Assistant internet access and retry
  after a provider rate limit expires.
- **Availability is unknown:** NDW may not have supplied connector availability
  for that station. CurbCharge does not infer it from operational status.
- **Price is unknown:** The provider may omit the tariff or publish conditional,
  conflicting, mixed-currency, or incomplete tax data.
- **Data looks old:** Compare the station's provider update timestamp with
  Home Assistant's last successful refresh timestamp.
- **A station disappeared:** Temporarily missing stations remain unavailable
  for two successful polls and are removed after the third.

## Privacy and diagnostics

Diagnostics omit station addresses and exact station coordinates, redact
manual coordinates from entry data, and round distances to the nearest
kilometre. CurbCharge does not log coordinates, credentials, or raw provider
responses. The integration does not request credentials from NDW. To download
diagnostics, open **Settings → Devices & services → CurbCharge**, open the
integration menu, and select **Download diagnostics**.

## Removing CurbCharge

Remove the integration from **Settings → Devices & services**. For a manual
installation, delete `custom_components/curbcharge` from the Home Assistant
configuration directory and restart Home Assistant.

## Development

Install the test tools and run:

```bash
pip install pytest pytest-homeassistant-custom-component pytest-cov ruff mypy
pytest --cov=custom_components/curbcharge --cov-fail-under=96
ruff check .
ruff format --check .
mypy
```

## Support

[Support CurbCharge on Ko-fi](https://ko-fi.com/matthijssn).

[dotnl]: https://english.ndw.nu/dataportals/dot-nl
[geojson-api]: https://docs.ndw.nu/data-uitwisseling/interface-beschrijvingen/dafne-api/dafne_api_consumer_pull/
[tariffs]: https://opendata.ndw.nu/charging_point_tariffs_ocpi.json.gz
