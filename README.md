# CurbCharge

CurbCharge discovers public EV charging points near a Home Assistant home, a
manually configured location, or a tracked Home Assistant entity. It creates
one device per charging point and reports provider-supplied availability,
connector counts, power, distance, operator, tariff data when unambiguous, and
a best-nearby recommendation.

## Requirements

CurbCharge requires Home Assistant 2024.12.0 or newer; this is the minimum
declared in `hacs.json`. Core 2024.12.0 provides `ConfigEntry.runtime_data` and
requires Python 3.12 or newer, matching the integration's APIs and Python
syntax. CI runs on Python 3.13 with the current Home Assistant test package; it
does not pin the test suite to Core 2024.12.0.

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
provider-reported `open` status is displayed separately; CurbCharge does not
infer connector availability from it or vice versa.

Tariffs are shown only when every connector group at a station resolves to the
same active currency and price components. Date-only OCPI restrictions are
evaluated using the Netherlands local date; session-dependent or time-window
restrictions remain unknown. Conflicting, missing, incomplete, or mixed-currency tariffs also
remain unknown rather than being guessed or shown as zero. The price and
status-summary sensors expose tariff attributes, including a stable
`tariff_status` (`available`, `not_reported`, `missing`, `conditional`,
`inactive`, `incomplete`, or `ambiguous`). When no single station-wide price
is available, the status-summary sensor includes a connector-group tariff
breakdown. OCPI price components are published before VAT; CurbCharge applies
the reported VAT percentage to present a gross rate and treats a missing VAT
rate as unknown.

The GeoJSON OpenAPI description labels `power_max` as kW, while current live
examples contain values such as `15000` for a 15 kW connector. CurbCharge
normalizes values above 1000 as watts; values at or below 1000 are treated as
kW. This follows the observed feed while accommodating the published unit
description.

### Belgium DATEX II (preview)

The Belgium adapter targets Eco-Movement's [DATEX II API][belgium-datex-api],
and can be selected in the config flow. Setup asks for an Eco-Movement API
token as a password field, validates it before creating the entry, stores it
only in config-entry data, and redacts it from diagnostics. Use this preview
only if you are authorized to access the API and have reviewed the applicable
terms. Eco-Movement issues these tokens; request access by emailing
[Eco-Movement support](mailto:support@eco-movement.com).

The `/locations` snapshot refreshes every 24 hours. Eco-Movement documents
status updates on change and within one minute, but CurbCharge reads them on
its scheduled polls; the default interval is five minutes and quotas may add
further delay. The limits are one request per 10 seconds per source IP, 30
location requests per hour per token, and two status requests per minute per
EVSE per token. The provider uses a shared Home Assistant request limiter; a
refresh can therefore take several minutes when many EVSEs need status updates.
A `Retry-After` response pauses requests from all Belgium entries sharing that
limiter. If a token expires or is revoked, the integration prompts for a
replacement token and validates it before updating the entry.

Eco-Movement access approval, applicable terms/licensing, and live Belgian
payload compatibility have not been verified. Treat them as release blockers.
Tests use sanitized synthetic examples based on the published DATEX profile;
no recorded production response is committed.
Missing status remains unknown; failed updates make coordinator entities
unavailable instead of inferring charger state. See the
[Transportdata.be dataset][belgium-dataset].

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

## Lovelace card

The single-station card is bundled with the integration; it is not a separate
HACS plugin or a file that needs to be copied into `www`. The integration
serves it at `/curbcharge/curbcharge-card.js`. In **Settings → Dashboards →
Resources**, add that URL as a JavaScript module.

Existing dashboards using `/local/curbcharge-card.js` only need a one-time
resource URL change to `/curbcharge/curbcharge-card.js`; the card configuration
does not need to change. After updating the resource, you can remove
`/config/www/curbcharge-card.js` if no other dashboard uses it. Future card
updates arrive with the integration after Home Assistant reloads or restarts.

Add the card to a dashboard and set `entity` to the station's **Status summary**
sensor:

```yaml
type: custom:curbcharge-card
entity: sensor.your_station_status_summary
```

The card editor lets you choose the station entity, use the standard or compact
layout, and show or hide price, connector details, station details, and update
times. With only `entity` set, all metric groups are shown by default. The
secondary details are available in an expandable section that starts collapsed.
`show_price` controls the price metric; `show_connector_details` controls
occupied, unavailable, and total counts;
`show_station_details` controls maximum power, distance, and provider-reported
open status; `show_update_times` controls the provider and Home Assistant
timestamps.
The same options can be set in YAML:

```yaml
type: custom:curbcharge-card
entity: sensor.your_station_status_summary
layout: compact
show_price: true
show_connector_details: true
show_station_details: false
show_update_times: false
```

The card reads only that Home Assistant entity; it makes no provider requests.
It displays the station name, availability, available/occupied/unavailable/total
connector counts, maximum power, price when known, distance, provider-reported
open status, the provider update time, and the last successful integration
update. Unknown values remain unknown, and unavailable station data is shown as
unavailable. Labels follow the Home Assistant language in English, Dutch,
French, or German.

## Configuration

Add **CurbCharge** from **Settings → Devices & services → Add integration**.
Choose one of three location sources:

- **Home** uses Home Assistant's configured home coordinates when the
  integration sets up; they are not copied into the config entry.
- **Manual** uses the latitude and longitude you enter. These fixed coordinates
  are stored locally because they are needed for each search.
- **Entity** uses a generic Home Assistant entity selector. Select any entity
  that currently exposes valid numeric `latitude` and `longitude` attributes;
  it is not restricted to the `device_tracker` domain. CurbCharge stores the
  selected entity ID locally and resolves its coordinates at runtime, without
  persisting the changing coordinates.

Use **Reconfigure** on an existing entry to change its location source or to
select a replacement if an Entity source was renamed or removed.

Choose a search radius from 100 to 10,000 metres and a maximum of 1 to 50
stations (default 10), then select NDW DOT-NL or the Belgium preview. Belgium
setup also asks for the Eco-Movement API token and validates connectivity
before creating the entry. The default polling interval is five minutes; the
options flow permits intervals of one minute or longer, plus changes to the
radius, station limit, and whether to include stations for which no
availability data was returned. For Entity sources, the options flow also
offers a movement threshold that defaults to 100 metres; set it to `0` to
disable the threshold. Favourites can also be selected by their displayed
names. They are stored per config entry, continue to be refreshed outside the
search radius, and are added beyond the normal nearby station limit. The
provider is selected during setup and cannot be switched from the options flow.
See the
[Belgium preview setup](#belgium-preview-setup) steps before choosing that
provider.

### Belgium preview setup

1. Request an authorized token from [Eco-Movement support](mailto:support@eco-movement.com)
   before setup. CurbCharge cannot issue a token; see the official
   [DATEX II API documentation][belgium-datex-api] for access and terms.
2. Add CurbCharge from **Settings → Devices & services → Add integration**.
   Choose Home Assistant's home location, enter a manual location, or select
   an entity with valid coordinates, then set the search radius and maximum
   station count.
3. Select **Eco-Movement Belgium**. On the next step, paste the token into the
   password field; CurbCharge validates it before creating the config entry.
4. Keep the token private. It is stored in the config entry and redacted from
   diagnostics; never include it in screenshots, logs, or issue reports. If
   the token expires or is revoked, Home Assistant starts reauthentication so
   you can enter a replacement.

The following original mockups use synthetic values; they are illustrative
examples, not captures of a live Home Assistant session.

![Illustrative Belgium setup with the provider selected and API token masked](./docs/images/belgium-setup-example.svg)

![Illustrative Belgium entities with country/provider and search scope](./docs/images/belgium-entities-example.svg)

## Supported stations

CurbCharge's supported live coverage is public charging points in the
Netherlands published by NDW DOT-NL. Belgium DATEX II is available as a preview
for authorized users, but is not considered release-ready until access,
licensing, quotas, and real-payload compatibility are verified. CurbCharge
reports provider data; it does not connect to or control physical chargers.
An Entity location moves the search center but does not extend provider
coverage. Each poll still applies the selected provider's geographic coverage,
configured search radius, and station limit.

## Entities

Each charging point is one Home Assistant device. CurbCharge creates:

- **Available**: on when the provider reports at least one available connector.
- **Available connectors**, **Occupied connectors**, **Unavailable
  connectors**, and **Total connectors**.
- **Maximum charging power**, **Price per kWh**, and **Distance**.
- **Status summary**, including connector types, operator, address, provider
  status, tariff details, coordinates, and provider update timestamps.
- The Status summary's `is_favorite` attribute identifies stations selected
  as favourites for this config entry.
- Integration-level **Best nearby charger**, **Nearest available charger**,
  **Cheapest available charger**, **Fastest available charger**, **Nearby
  charging units**, and **Nearby available connectors** sensors. The
  charging-unit count covers
  current, non-stale records in that entry's configured search area (NDW
  charge-point records in the Netherlands and EVSEs in Belgium), not physical
  sites, available chargers, or nationwide coverage. Its attributes report
  unknown availability and stale records, provider, search radius, result cap,
  and whether results may be truncated.
- The available-connectors count sums only connector counts explicitly reported
  as available at current stations. It exposes known, unknown, and stale
  station counts; missing fresh availability is unknown, not zero. A Belgian
  EVSE's overall available status does not imply that its connector count is
  known.
- **Nearby connector availability** is the weighted percentage of known
  available connectors over the corresponding known total counts, rounded to
  one decimal place; it is a ratio of summed counts, not an average of station
  percentages. Connector groups with unknown availability and stale stations
  are excluded. Coverage is the known denominator over all fresh,
  provider-reported connector totals; attributes also disclose unreported
  totals. If no known denominator remains, the sensor is unknown rather than
  0%.

These statistics are scoped to one config entry's bounded search area.
Separate entries, including overlapping searches in the same country, remain
separate and must not be summed as national coverage.

Favourites remain represented as devices when they are outside the configured
radius or the normal result cap. Nearby aggregate sensors remain radius-scoped:
they include fresh in-radius favourites but exclude remote favourites. Best
nearby gives no favourite bonus; fresh in-radius favourites compete under the
normal ranking, while remote or stale favourites are excluded.

For each availability group reported by NDW, the station also has a
disabled-by-default connector-group sensor. The provider groups connectors by type and
power characteristics; these are not individual physical connector IDs.

The occupied sensor is unknown if the provider only reports available and
total counts and some connectors are not available. This avoids treating a
reserved or out-of-service connector as occupied. The unavailable count is
`total - available`, which is the only additional count directly supported by
the NDW availability response.

**Best nearby** keeps its existing ranking: lower known prices when all
candidate prices use the same currency, then shorter distance, more available
connectors, higher power, and a stable station identifier. Its `reason`
attribute is a stable reason code.

The three additional selectors offer deterministic alternatives. Each requires
a fresh station with a known `available_connectors` count greater than zero;
unknown availability, zero connectors, and stale records do not qualify.
**Nearest available charger** requires a known distance and chooses the
shortest. **Cheapest available charger** ignores unpriced stations, compares
only unambiguous station-level prices in one currency, and returns no station
if no price is known or known prices use multiple currencies. **Fastest
available charger** chooses the highest positive station-level `max_power_kw`.
This is the station's highest reported connector-group power, not a claim about
the currently available physical plug.

Ties are deterministic: nearest uses provider and station ID after distance;
cheapest uses known/shorter distance after price; fastest uses distance after
power. Provider and station ID break any remaining tie. Each selector sensor's
state is the selected station name, or `unknown` when there is no selection.
Attributes include station details, a stable `reason` code, and the last
successful update time. These sensors use the existing coordinator data and
make no extra provider requests.

When a tracked search center moves, newly matching stations are added on the
next successful refresh. A non-favourite station absent from three consecutive
successful refreshes becomes unavailable after the first miss and its entities
and device are removed after the third. Saved favourites remain as unavailable
devices through provider misses; removing a favourite resumes the three-refresh
removal behavior. Provider failures and invalid location updates do not count
as station absences or replace the last successful coordinator data.

## Data updates

CurbCharge refreshes station data with a shared `DataUpdateCoordinator`. The
coordinator calls the provider once per cycle; entities never make requests.
The default interval is five minutes and can be reduced to one minute in
options. NDW may paginate GeoJSON results, and tariffs come from a separate
OCPI file cached for six hours. The provider client may therefore make
additional HTTP requests when more pages or uncached tariffs are needed.
NDW's station `last_updated` time is shown separately from Home Assistant's
last successful refresh time; upstream data can be delayed even immediately
after a successful poll. Favourites outside the normal NDW search results use
additional small bounding-box requests; the provider client spaces these
requests to respect its rate limit. Belgium favourites use the cached location
snapshot and status requests through the shared limiter, so a large favourite
list can lengthen a refresh.

For an Entity source, CurbCharge samples coordinates during the normal
coordinator refreshes. It does not subscribe to every entity state change or
make a provider request for each GPS update. The movement threshold compares
each valid coordinate with the last accepted search center: smaller fixes
accumulate relative to that center, so GPS jitter below the threshold does not
move the search. With a threshold of `0`, any changed coordinates are accepted
at the next poll. Availability polling continues at its configured cadence
whether or not the center moves. A move can take up to one polling interval,
plus provider latency or rate-limit delays, to affect results; this is not
real-time tracking.

If the selected entity is unavailable or has invalid coordinates at a poll,
CurbCharge skips the provider request, retains its last successful data, and
marks station entities unavailable. It tries again on the next refresh;
updates resume when valid coordinates return.

## Use cases and automations

### Charger recommendations

Add an **Entities** card and select **Nearest available charger**,
**Cheapest available charger**, **Fastest available charger**, and optionally
**Best nearby charger** in the Home Assistant card editor. The entity IDs are
specific to your config entry; for YAML dashboards, replace the example IDs
with the IDs shown in your Home Assistant entity picker:

```yaml
type: entities
title: Charger recommendations
entities:
  - entity: sensor.your_nearest_available_charger
  - entity: sensor.your_cheapest_available_charger
  - entity: sensor.your_fastest_available_charger
  - entity: sensor.your_best_nearby_charger
```

Automations can use the selected station name as the sensor state and read
distance, price, currency, power, or the selection reason from its attributes.
For example, notify when the cheapest available station changes:

```yaml
alias: Notify about the cheapest available charger
trigger:
  - platform: state
    entity_id: sensor.your_cheapest_available_charger
condition:
  - condition: template
    value_template: >
      {{ trigger.to_state is not none
         and trigger.to_state.state not in ['unknown', 'unavailable'] }}
action:
  - service: persistent_notification.create
    data:
      title: Cheapest available charger
      message: >
        {{ trigger.to_state.state }} is
        {{ trigger.to_state.attributes.price_per_kwh }}
        {{ trigger.to_state.attributes.currency }} per kWh,
        {{ trigger.to_state.attributes.distance_m }} m away.
mode: single
```

The values are recalculated with the regular coordinator refresh, so an
automation responds to poll updates rather than every provider or GPS event.

### Availability notifications

Import the [CurbCharge availability notification Blueprint](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fmatthijssn%2Fha_curbcharge%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fcurbcharge%2Fnotify_when_charging_point_available.yaml)
into Home Assistant. Blueprint import is separate from installing CurbCharge
through HACS. Create an automation from the imported Blueprint, select one or
more station **Status summary** sensors, then choose your preferred Home
Assistant notification action.

The Blueprint triggers only on a known `none_available` → `available`
transition. Startup, `unknown`/`unavailable` recovery, and provider outages do
not count as a charger becoming available. You can require the station to
remain available for a minimum duration; this timer resets if Home Assistant
restarts or the automation reloads.

The optional provider-data-age check is off by default. If enabled, the
Blueprint reads the selected station's provider `last_updated` timestamp and
suppresses the action when it is missing, invalid, in the future, or older than
the configured maximum age. Provider timestamps can describe a record update,
not necessarily an availability change, and the data provider does not promise
a fixed maximum age. Set this guard to a threshold appropriate for your use;
it is not a guarantee that the station is currently available. Polling also
adds latency, so notifications are not real-time.

In your selected action, the Blueprint provides `station_name` and
`station_entity` variables. To create a simple manual automation instead,
listen for the same explicit Status summary transition:

```yaml
alias: Notify when a charging station is available
trigger:
  - platform: state
    entity_id: sensor.your_station_status_summary
    from: "none_available"
    to: "available"
action:
  - service: persistent_notification.create
    data:
      title: Charging station available
      message: "{{ trigger.to_state.attributes.station_name }} now reports an available connector."
mode: single
```

## Services

CurbCharge is read-only and registers no custom services, device triggers, or
conditions. The Blueprint uses the existing Status summary state and a
notification action selected by the user.

## Troubleshooting

- **No stations appear:** Check the configured location source, radius,
  and that the selected provider has coverage for that area.
- **Entity location is unavailable:** Check that the selected entity still
  exists and currently reports valid latitude and longitude attributes. An
  unavailable or invalid source skips provider requests until a later poll
  finds valid coordinates. If the entity was renamed or removed, use
  **Reconfigure** to select its replacement.
- **Setup cannot connect:** Check Home Assistant internet access and retry
  after a provider rate limit expires.
- **Belgium token rejected:** Confirm Eco-Movement issued the token for your
  account and that it has not expired or been revoked. Use the reauthentication
  flow for a replacement; do not include the token in a support request.
- **Belgium requests are rate limited:** Wait for the provider's `Retry-After`
  interval. All Belgium entries on the same Home Assistant host share the
  per-IP limiter; see the documented per-token limits above.
- **Belgium availability is unknown:** Static location data refreshes daily and
  dynamic status is requested per EVSE. A location can exist without a current
  status report; CurbCharge leaves that availability unknown.
- **NDW availability is unknown:** NDW may not have supplied connector availability
  for that station. CurbCharge does not infer it from operational status.
- **Price is unknown:** The provider may omit the tariff or publish conditional,
  conflicting, mixed-currency, or incomplete tax data.
- **Data looks old:** Compare the station's provider update timestamp with
  Home Assistant's last successful refresh timestamp.
- **A station disappeared:** Temporarily missing stations remain unavailable
  for two successful polls and are removed after the third. Saved favourites
  remain unavailable until returned or unfavourited.

## Privacy and diagnostics

Diagnostics omit station addresses and exact station coordinates, redact
manual coordinates, Entity source IDs, and saved favourite references from
entry data, and round distances to the nearest kilometre. In Entity mode, the
selected entity ID is stored locally in the config entry and redacted from
diagnostics; its changing coordinates are not persisted or included in
diagnostics. Favourite IDs, names, and coordinates are not included in the
diagnostic station list. CurbCharge does not log entity IDs, coordinates,
credentials, or raw provider responses. The integration does not request
credentials from NDW. To download diagnostics, open **Settings → Devices &
services → CurbCharge**, open the integration menu, and select **Download
diagnostics**.

## Removing CurbCharge

Remove the integration from **Settings → Devices & services**; this also
removes its stored Belgium token. For a manual installation, delete
`custom_components/curbcharge` from the Home Assistant configuration directory
and restart Home Assistant.

## Development

The protected `main` branch requires the `test`, `hassfest`, and `hacs` check
contexts. The Hassfest and HACS workflows use distinct job contexts so each
validator can block merges independently.

Install the test tools and run:

```bash
pip install pytest pytest-homeassistant-custom-component pytest-cov ruff mypy
pytest --cov=custom_components/curbcharge --cov-fail-under=96
ruff check .
ruff format --check .
mypy
```

## Preparing a release

Home Assistant and HACS require a concrete version in `manifest.json`; do not
replace it with a template placeholder. To prepare a release, run the
**Release version** workflow from `main` and enter the intended GitHub tag,
such as `v0.1.2`. The workflow validates the tag, updates the manifest on a
`release/vX.Y.Z` branch, and provides a compare link for a pull request.

Review and merge that PR before creating the GitHub release from the same tag
on `main`. When the release is published, the workflow verifies that the tag
matches the manifest version and points to a commit on `main`.

## Support

[Support CurbCharge on Ko-fi](https://ko-fi.com/matthijssn).

[dotnl]: https://english.ndw.nu/dataportals/dot-nl
[geojson-api]: https://docs.ndw.nu/data-uitwisseling/interface-beschrijvingen/dafne-api/dafne_api_consumer_pull/
[tariffs]: https://opendata.ndw.nu/charging_point_tariffs_ocpi.json.gz
[belgium-datex-api]: https://developers.eco-movement.com/docs/belgium-datex-ii-api
[belgium-dataset]: https://transportdata.be/en/dataset/public-charging-infrastructure-static-dataset-selected-cpos-datex-ii
