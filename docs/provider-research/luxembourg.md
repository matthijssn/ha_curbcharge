# Luxembourg (Chargy) provider eligibility

Source assessment for [#327](https://github.com/matthijssn/ha_curbcharge/issues/327),
checked on 2026-11-04. **Eligible with unknown plug types**, explicitly approved
by the maintainer after the source gate disproved the planned type-code mapping.

## Publisher, access and licence

The [Chargy dataset][dataset] on data.public.lu is published by the certified
[Chargy organisation][organisation] under [CC0 1.0][cc0]. The
[metadata API][metadata] describes a KML refreshed every five minutes.
This is Chargy's network snapshot, including SuperChargy, not a guarantee of
coverage of every Luxembourg operator. Attribution: Data: Chargy,
data.public.lu (CC0).

Use only the [stable keyless portal resource][resource]. On 2026-11-04 it
returned HTTP 302 followed by HTTP 200 without user credentials. The redirect
target carries a publisher-owned key. Never copy, persist, log, or include that
target URL in diagnostics, exceptions, fixtures, or documentation. The adapter
does not inspect the final response URL, and replaces transport errors with
generic messages without exception chaining. No publisher key is bundled.

## Bounded measurements

The initial source gate used two downloads **305 seconds apart**:

| Measurement | First | Second |
| --- | ---: | ---: |
| Bytes | 697,042 | 697,025 |
| Placemarks | 526 | 526 |
| Devices | 976 | 976 |
| Connectors | 1,893 | 1,893 |

Device and connector IDs were unique in the first sample. All device IDs,
placemark names and device-to-connector ID relationships were unchanged in the
second sample; no device was added or removed. This observation supports source
IDs for identity but is not a publisher guarantee of indefinite ID stability.
One station is created per device, identified as `chargy:{device.id}`.
The device name (for example `CP2500`) is its display EVSE reference, not a
claimed standardized international EVSE ID.

An earlier planning probe on 2026-11-03 observed HTTP 429 after three downloads
in about four minutes, persisting a minute later. No quota was documented.
A follow-up check on 2026-11-04 at 07:51:02 UTC returned portal HTTP 302 then
publisher HTTP 200. The maintainer then explicitly authorized an exception to
the research request spacing: at most three additional requests, stopping on
429 without retries. The first two returned 200 at 07:52:52 and 07:53:02 UTC;
the third returned **publisher HTTP 429 at 07:53:12 UTC, with no Retry-After
header**. Each request followed the portal's 302. The 429 response contained
only `Connection`, `Content-Length`, `Content-Type` and `Date` headers.
The probe stopped immediately, with no further requests.

This verifies the need for the fallback cooldown, not a guaranteed quota,
recovery duration or promise that future 429 responses never include
Retry-After. The runtime five-minute request floor is unchanged. Offline tests
cover both numeric and HTTP-date Retry-After, absence, malformed headers,
cooldown escalation and sharing.

All entries in one Home Assistant instance share a lock, neutral parsed
snapshot and at least 300 seconds between download starts, including validation
and failed requests. Cached snapshots are reused within that interval;
distances are recalculated for each search. HTTP 429 honors Retry-After with a
minimum 300-second delay; absent/invalid headers use an increasing cooldown
from 300 seconds to a 3,600-second cap. An explicit longer Retry-After is not
shortened. Successful refresh resets that cooldown.
There are no automatic network retries. A failed or cancelled refresh is
reported as failure, not replaced with the old snapshot as a successful update.

## Schema and mapping

KML uses `http://www.opengis.net/kml/2.2`. A placemark contains a name,
address, HTML description, aggregate style, coordinates and `ExtendedData`.
`CPnum` is a location-level count; repeated `Data name="chargingdevice"`
values are JSON records. A device has `id`, `name`, `numberOfConnectors` and
`connectors`. A connector has `id`, `name`, `maxchspeed` (kW), `connector`
and `description` (its status). No tariffs or provider timestamps were found.
The adapter leaves prices and timestamps unknown rather than substituting
fetch time, and reports tariff status `not_reported`.

The numeric `connector` field is **not a verified plug-type enum**.
Values 1 through 10 were observed; 1 and 2 occur on both 22 kW and
160-400 kW devices and appear to be socket ordinals. The HTML misleadingly
labels even SuperChargy's 350-400 kW connectors as "Type 2". Consequently,
neither source field is used to infer a plug standard, even from power.
`connector_type` and `connector_type_id` remain unset. The source connector
ID and socket ordinal are preserved together as
`{connector.id}:socket:{connector.connector}`. Neither is presented as a plug
type. Aggregate HTML counts and `styleUrl` never override individual statuses.

Observed connector status vocabulary (union of both samples):

| Source `description` | Normalized connector status |
| --- | --- |
| `AVAILABLE` | available |
| `CHARGING` | occupied |
| `PREPARING` | occupied |
| `FINISHING` | occupied |
| `SUSPENDED_EV` | occupied |
| `SUSPENDED_EVSE` | occupied |
| `FAULT` | out of service |
| `OFFLINE` | unknown |
| `UNAVAILABLE` | unknown |
| Any future/unmapped string | unknown |

Preparing, finishing and suspended session states are treated as occupied,
not available. Offline or unspecified unavailability does not prove a
physical fault or occupancy. If any connector status is unknown, the exact
device-level available count is unknown. No source EVSE-level status exists,
so `evse_status` is unset; station availability is derived from its connectors.

Invalid XML/JSON, missing required fields, duplicate IDs and inconsistent
connector counts fail the refresh explicitly. A location with invalid
coordinates is skipped with a generic warning that contains no source payload;
a snapshot with no valid devices fails. Tests use synthetic data only.

## Exclusions

The other portal dataset, supplied by Eco-Movement as DATEX II, had quarterly
metadata, a tokenized URL and an unspecified licence at planning time. It is
not used here; unspecified licensing is not a finding of prohibited use.
Non-Chargy operators, tariffs, plug-type inference and invented timestamps are
out of scope.

[dataset]: https://data.public.lu/fr/datasets/bornes-de-chargement-publiques-pour-voitures-electriques/
[metadata]: https://data.public.lu/api/1/datasets/bornes-de-chargement-publiques-pour-voitures-electriques/
[resource]: https://data.public.lu/fr/datasets/r/22f9d77a-5138-4b02-b315-15f306b77034
[organisation]: https://data.public.lu/fr/organizations/chargy/
[cc0]: https://creativecommons.org/publicdomain/zero/1.0/
