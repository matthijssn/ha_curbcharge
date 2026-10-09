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

## Entities

Each charging point is one Home Assistant device. CurbCharge creates:

- **Available**: on when the provider reports at least one available connector.
- **Available connectors**, **Occupied connectors**, **Unavailable
  connectors**, and **Total connectors**.
- **Maximum charging power**, **Price per kWh**, and **Distance**.
- **Status summary**, including connector types, operator, address, provider
  status, tariff details, coordinates, and provider update timestamps.
- One integration-level **Best nearby charger** sensor.

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

## Privacy and diagnostics

Diagnostics omit station addresses and exact station coordinates, redact
manual coordinates from entry data, and round distances to the nearest
kilometre. CurbCharge does not log coordinates, credentials, or raw provider
responses. The integration does not request credentials from NDW.

## Development

Install the test tools and run:

```bash
pip install pytest pytest-homeassistant-custom-component ruff
pytest
ruff check .
```

## Support

[Support CurbCharge on Ko-fi](https://ko-fi.com/matthijssn).

[dotnl]: https://english.ndw.nu/dataportals/dot-nl
[geojson-api]: https://docs.ndw.nu/data-uitwisseling/interface-beschrijvingen/dafne-api/dafne_api_consumer_pull/
[tariffs]: https://opendata.ndw.nu/charging_point_tariffs_ocpi.json.gz
