# Dashboards and automations

## Lovelace card

The CurbCharge card is bundled with the integration; it is not a separate HACS
plugin or a file that needs to be copied into `www`. The integration serves it
at `/curbcharge/curbcharge-card.js`. In **Settings → Dashboards → Resources**,
add that URL once as a JavaScript module. HACS updates the card with the
integration; reload or restart Home Assistant after updating.

Existing dashboards using `/local/curbcharge-card.js` only need a one-time
resource URL change to `/curbcharge/curbcharge-card.js`; the card configuration
does not need to change. After updating the resource, you can remove
`/config/www/curbcharge-card.js` if no other dashboard uses it. Future card
updates arrive with the integration. CurbCharge does not add a HACS frontend
item, dependency, or manifest setting for this bundled card.

### Native dashboard (no extra frontend cards)

A native dashboard can use Home Assistant's built-in Markdown and Entities
cards to show the integration-level **Best nearby** recommendation, station
details, and nearby aggregate sensors. The
[complete copyable YAML](../examples/curbcharge-dashboard.yaml) is ready to add
to a dashboard. Replace each `sensor.your_...` value with the corresponding
entity ID from your Home Assistant entity picker. Best nearby uses
CurbCharge's multi-factor ranking; it is not necessarily the closest station.
Use **Nearest available charger** when distance alone should determine the
selection.

### Native map and tap-to-navigate links

The built-in Map card can plot any entity with `latitude` and `longitude`
attributes. Per-station **Status summary** sensors already provide them;
**Best nearby** provides the selected destination coordinates. In the
[complete Map card example](../examples/curbcharge-map.yaml), replace both
example IDs with the entities from your instance, or remove the Status summary
row if it would duplicate the Best nearby marker. The card may also show Home
Assistant's Home zone; it is not used as a directions origin.

For one-tap links, use the built-in Markdown card template in
[`examples/curbcharge-navigation.yaml`](../examples/curbcharge-navigation.yaml).
It reads the selected entity's destination coordinates, using its address only
if coordinates are absent. Change the entity ID in both `entity_id` and the
template to use a per-station Status summary sensor instead.

Best nearby links appear only for a selected, available recommendation. A
Status summary with unknown connector availability can still provide directions
when it has valid coordinates or an address. No link is shown for a missing
destination or an unavailable/stale entity. The URLs omit route-origin
parameters, so they do not send Home Assistant's home, search-area, or vehicle
coordinates; a compatible map app can use the mobile device's current location.
The `geo:` link opens a map location only and is not turn-by-turn navigation.

These HTTPS links are opened only after the user taps them. App handoff depends
on the operating system, browser, Companion App, and installed map apps.
Google Maps and Waze may open their installed app or fall back to the provider
website. Apple Maps links use Apple's unified Maps URL format on supported
Apple operating systems; other devices may handle them differently. The
optional `geo:` URI works only when the operating system has a compatible map
handler. These are expected fallbacks, not guarantees for every device.

Before relying on the links, use the Home Assistant Companion App on Android
and iOS to tap each provider link with its app installed and absent, confirm the
fallback behavior, and check that directions start from the mobile device. Also
verify that the `geo:` link opens a location without claiming to start
navigation.

See the Home Assistant [Map card](https://www.home-assistant.io/dashboards/map/)
and [Markdown card](https://www.home-assistant.io/dashboards/markdown/)
documentation, plus the provider formats for
[Google Maps](https://developers.google.com/maps/documentation/urls/get-started),
[Apple Maps](https://developer.apple.com/documentation/mapkit/unified-map-urls),
and [Waze](https://developers.google.com/waze/deeplinks).

The native view has no custom-card dependency. If you prefer the bundled
CurbCharge card's summary layout, set its `entity` to a station **Status
summary** sensor or the Best nearby recommendation sensor:

```yaml
type: custom:curbcharge-card
entity: sensor.your_best_nearby_charger
```

Existing YAML that sets only `entity` to a Status summary sensor remains
compatible. The card editor also offers standard or compact layout and controls
for price, connector details, station details, and update times. Secondary
details remain expandable and start collapsed. For YAML, the same options are
available:

```yaml
type: custom:curbcharge-card
entity: sensor.your_best_nearby_charger
layout: compact
show_price: true
show_connector_details: true
show_station_details: false
show_update_times: false
```

With only `entity` set, all metric groups are shown by default.
`show_price` controls price; `show_connector_details` controls occupied,
unavailable, and total counts; `show_station_details` controls address, maximum
power, distance, and provider-reported open status; `show_update_times` controls
provider and Home Assistant timestamps.

The card reads only the selected Home Assistant entity; it makes no provider
requests. It displays the station name and operator when available, address,
availability, connector counts, maximum power, distance, price/currency when
known, and update times. Labels follow the Home Assistant language in English,
Dutch, French, or German.
When the entity has a valid destination, a collapsed **Navigate** disclosure
offers explicit Google Maps, Apple Maps, Waze, and open-map links; nothing opens
until the user taps a link. It is hidden for an unavailable/stale entity or an
unselected Best nearby result. A Status summary with unknown availability can
still offer links when its destination is valid.

For recommendation sensors, `provider_last_updated` is the provider station
record time and `last_successful_update` is the last successful Home Assistant
refresh. Best nearby's legacy `last_updated` meaning is preserved and should
not be used as the provider timestamp. Its `result_status` distinguishes
`selected`, `no_current_stations`, `unknown_availability`, `none_available`,
and `unavailable`. Unknown values are not zero; when the entity is unavailable,
the bundled card does not show old measurements as current. The Status summary
and Best nearby also expose `provider_timestamp_source` and
`provider_data_age_seconds`; see [Data updates](usage.md#data-updates) for their
semantics.

### Optional: dynamic station list with Auto-Entities

Auto-Entities is a separate community frontend card. Install it separately
through HACS Frontend and register the JavaScript module resource specified by
its own installation instructions. This optional example is not required by
CurbCharge and is separate from the native dashboard above:

```yaml
type: custom:auto-entities
card:
  type: entities
  title: CurbCharge station summaries
filter:
  include:
    - entity_id: "sensor.*_status_summary"
sort:
  method: name
```

Adjust the filter to include only the stations or areas you want to list.

### Illustrations

These desktop and mobile mockups use synthetic values only; they are not live
Home Assistant captures and contain no real location data.

![Synthetic desktop dashboard with a selected example station and nearby aggregate sensors](images/curbcharge-dashboard-selected.svg)

![Synthetic mobile dashboard showing no-current-station and unavailable edge states](images/curbcharge-dashboard-mobile-edge.svg)

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

### Reading observed availability transitions

For **Available**, `on` and `off` are known provider reports. `unknown` means
the latest snapshot has no usable availability; `unavailable` means the entity
cannot currently report, for example because its station is stale or a
coordinator refresh failed. Neither is an `off` state. A missed station becomes
unavailable after a successful poll; non-favourites are removed after three
consecutive successful misses, while favourites remain unavailable. Failed
refreshes do not count as misses.

To inspect how long the current state has been known, paste this guarded
template into **Developer tools > Template**. It returns seconds only for a
current `on` or `off` state:

```jinja
{% set entity_id = 'binary_sensor.your_station_available' %}
{% set current = states(entity_id) %}
{% if current in ['on', 'off'] %}
  {% set state = states.binary_sensor.your_station_available %}
  {{ (as_timestamp(now()) - as_timestamp(state.last_changed)) | int }}
{% else %}
  No known availability state
{% endif %}
```

`last_changed` is the time Home Assistant observed that transition, not the
provider's actual transition time. CurbCharge does not restore a station's
prior live state after a Home Assistant restart or config-entry reload; a fresh
snapshot establishes the current state, so this duration does not span a
restart. Earlier transitions may still be visible in Recorder-backed History
while they remain within its retention.

In an automation, `trigger.from_state` and `trigger.to_state` are the state
objects on either side of the trigger. Guard against missing or non-binary
states so startup, recovery from a gap, and attribute-only updates are not
reported as known availability transitions:

```yaml
alias: Notify about an observed availability transition
trigger:
  - platform: state
    entity_id: binary_sensor.your_station_available
condition:
  - condition: template
    value_template: >
      {{ trigger.from_state is not none
         and trigger.to_state is not none
         and trigger.from_state.state in ['on', 'off']
         and trigger.to_state.state in ['on', 'off']
         and trigger.from_state.state != trigger.to_state.state }}
action:
  - service: persistent_notification.create
    data:
      title: Station availability changed
      message: >
        Home Assistant observed {{ trigger.from_state.state }} to
        {{ trigger.to_state.state }} at {{ trigger.to_state.last_changed }}.
mode: single
```

The trigger timestamp is Home Assistant's observation time. This example reads
built-in state objects only; it does not create a timestamp entity or save a
separate transition history.

### Historical availability and connector counts

The **Available** binary sensor gives a per-station `on`/`off` timeline from
provider snapshots. The **Availability percentage** sensor is the current
snapshot expressed as `100%`, `0%`, or unknown; it does not calculate the
percentage of time a charger was available. The nearby connector percentage is
a separate, search-area aggregate weighted by known connector counts.

To calculate how long a station was reported available, create Home Assistant's
built-in [History Stats](https://www.home-assistant.io/integrations/history_stats/)
helper for its **Available** binary sensor. Choose `time` for hours in the
selected period or `ratio` for the portion of that period spent in `on`.
For example, this YAML configuration creates a daily ratio:

```yaml
sensor:
  - platform: history_stats
    name: Station available today
    entity_id: binary_sensor.your_station_available
    state: "on"
    type: ratio
    start: "{{ today_at() }}"
    end: "{{ now() }}"
```

The helper uses Home Assistant's [History](https://www.home-assistant.io/integrations/history/)
data, which is stored by [Recorder](https://www.home-assistant.io/integrations/recorder/),
and is limited by its retention. Unknown provider availability appears as
unknown, while stale station data or a coordinator failure can make the
entities unavailable. These are gaps, not confirmation that the station was
unavailable; review the source timeline before interpreting a ratio as complete
uptime.

Use a [History Graph](https://www.home-assistant.io/dashboards/history-graph/)
for recent state changes and connector counts:

```yaml
type: history-graph
title: Station availability and connector counts
hours_to_show: 24
entities:
  - binary_sensor.your_station_available
  - sensor.your_station_available_connectors
  - sensor.your_station_total_connectors
```

For longer-term hourly trends, use Home Assistant's
[Statistics Graph](https://www.home-assistant.io/dashboards/statistics-graph/)
with the per-station percentage sensor:

```yaml
type: statistics-graph
title: Station availability, hourly
days_to_show: 30
period: hour
chart_type: line
stat_types:
  - mean
entities:
  - sensor.your_station_availability_percentage
```

Connector counts have different units, so graph them in a separate card:

```yaml
type: statistics-graph
title: Connector counts, hourly
days_to_show: 30
period: hour
chart_type: line
stat_types:
  - mean
entities:
  - sensor.your_station_available_connectors
  - sensor.your_station_total_connectors
```

Home Assistant's [Recorder](https://www.home-assistant.io/integrations/recorder/)
keeps detailed state history for 10 days by default (`purge_keep_days`); this
can be changed in the Home Assistant Recorder configuration. Five-minute
statistics follow that retention, while hourly
long-term statistics for measurement sensors such as availability percentage
and connector counts are retained indefinitely. The hourly mean of the
percentage sensor is a trend of reported `0%`/`100%` values, not an exact
time-in-state calculation; it cannot show changes between provider polls.
For numeric sensors with long-term statistics, History Graph may use hourly
aggregates for older portions of its range; binary sensor transitions remain
limited to retained detailed history.

Recorder storage is managed by Home Assistant. Each enabled station entity
included in Recorder adds history and eligible statistics, so following more
stations increases database use. Choose Recorder retention and exclusions to
suit your instance; long-term statistics persist until removed or the database
is cleared. Replace the example entity IDs above with the IDs shown in your
Home Assistant entity picker.
