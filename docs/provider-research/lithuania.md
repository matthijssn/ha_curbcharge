# Lithuania (Via Lietuva) provider research

Source verification for [#329](https://github.com/matthijssn/ha_curbcharge/issues/329),
completed on 2026-11-04 before implementation. The source gates passed for a
bounded national snapshot and conservative tariff support.

## Official source and terms

Via Lietuva's [public data page][open-data] publishes the OCPI 2.3.0
locations feed and a live dynamic DATEX II feed. It states that data is
updated in real time and is published under both
[CC BY 4.0][cc-by] and [ODC-BY 1.0][odc-by]. The open-data page permits use
and sharing with attribution, a link to the licence, and a clear statement
that changes were made. This integration normalizes the source data; user
documentation links to both licences and states that modification.

The [producer documentation][submission] describes OCPI 2.3.0 locations,
tariffs, EVSE status and stable EVSE identifiers. Producer submission rules
are not treated as a consumer API contract. The [public map][map] displays
multiple operators, status, connector type and price; national data is
operator-reported and is not an independent completeness guarantee.

Anonymous JSON requests work with an identifying `User-Agent`. Some initial
requests without it returned HTTP 403; the cause is unknown. The provider sends
`User-Agent: CurbCharge`, but does not claim that this guarantees access or
work around an access denial. No public numeric consumer quota was found.
Requests that return 429 honor `Retry-After`, or receive a bounded fallback
cooldown; this research did not deliberately exhaust a quota.

## Location pagination and measured coverage

The endpoint is:

`https://ev.vialietuva.lt/ocpi/2.3.0/locations`

The consumer response uses an OCPI envelope with status code 1000 and
`X-Total-Count`. On 2026-11-04, a bounded complete traversal used explicit
`limit=1000` and `offset` values. The `Link` values are malformed: they repeat
question marks/parameters and the first page's link does not advance. The
adapter therefore ignores `Link` and advances offsets by the requested page
size, not by the number of published records returned.

| Offset | Published locations returned | Body bytes |
| ---: | ---: | ---: |
| 0 | 886 | 2,290,299 |
| 1000 | 777 | 1,290,044 |
| 2000 | 136 | 316,614 |
| 3000 | 173 | 448,083 |
| 4000 (terminal check) | 0 | Empty response |

`X-Total-Count` was consistently **3291**, while the four windows contained
**1,972 unique published location records**. All returned records had
`publish=true` and physical country `LTU`; no duplicate
`(country_code, party_id, id)` identities occurred. Thus 3291 is an underlying
raw record/window bound, not a count of the published locations returned.
Short pages are not terminal: the request at offset 2000 returned 136 records
and the next offset still contained 173. The terminal request at offset 4000
returned an empty successful response.

Location bodies totaled **4,345,040 bytes**. Together with the complete tariff
response below, the measured refresh used **6,037,891 bytes**, below the
16 MiB response budget and 20-page maximum. Runtime checks enforce both
bounds, stable totals, page sizes, duplicate IDs and a complete offset
traversal before publishing a snapshot.

To check identity stability, two offset-zero windows were fetched **305
seconds apart**. Both returned 886 locations, 3,236 EVSE/connector
relationships, the same source total and the same SHA-256 over normalized
location/EVSE/connector identity relationships. Status counts changed between
the snapshots while connector standards and identities remained stable.

The full traversal contained **5,749 EVSE status records** and **5,751
connector records**. Observed EVSE states:

| State | Count |
| --- | ---: |
| AVAILABLE | 4,308 |
| CHARGING | 562 |
| OUTOFORDER | 609 |
| INOPERATIVE | 215 |
| REMOVED | 25 |
| RESERVED | 1 |
| UNKNOWN | 29 |

Observed connector standards were `IEC_62196_T2` (3,205),
`IEC_62196_T2_COMBO` (2,345), and `CHADEMO` (201). The provider normalizes
`max_electric_power` from watts to kW. EVSE status is not connector status:
individual connector occupancy and availability counts remain unknown.
Removed EVSEs are excluded. Foreign operators are included when the physical
location country is `LTU`.

Location and EVSE identifiers are integers in the measured feed. Identity is
scoped by operator country, party, location ID and EVSE UID; connector IDs
are scoped below the EVSE. Strings are accepted as well, but booleans and
invalid integer IDs are rejected. Timezone-less `last_updated` values remain
unknown; no response-envelope or fetch timestamp is substituted.

## Tariffs and exact links

The tariff endpoint is:

`https://ev.vialietuva.lt/ocpi/2.3.0/tariffs`

The endpoint returned **1,769 tariffs / 1,692,759 bytes** in one OCPI response,
with no `Link` or total-count header. All **5,720 connector tariff
references** in the complete location traversal resolved to an ID in this
tariff snapshot, and all IDs matched the same country. The tariff records had
1,769 unique `(country_code, party_id, id)` keys and no duplicate global IDs.

There is a source inconsistency: **2,931** references match the exact
`(country_code, party_id, tariff_id)` tuple; **2,789** reference IDs have a
different tariff `party_id` despite a unique tariff ID in the same country.
The implementation first uses an exact namespace match, then permits a
same-country ID fallback only if that ID resolves uniquely in the current
complete tariff snapshot. Missing IDs remain missing; duplicate/ambiguous
same-country IDs remain ambiguous. It never assumes tariff IDs are globally
unique without rechecking the current data.

Observed `tax_included` values were `YES` (780), `NO` (116), and null (873).
All 1,769 tariffs had null VAT values. Under
[OCPI 2.3.0 tariff semantics][ocpi-tariffs], `YES` means taxes are included,
`NO` means taxes must be added, and `N/A` means no tax is applicable. Therefore
the measured `YES` structured prices are gross; `NO` prices cannot be
converted to gross without an explicit component VAT; missing tax-inclusion
data is unknown. A future `N/A` is safe as reported. No country-default VAT
is inferred.

The source publishes structured ENERGY, FLAT, TIME and PARKING_TIME
components. ENERGY is per kWh; TIME is expressed per hour and normalized to
per minute by dividing by 60. `step_size` governs session billing granularity,
not the normalized unit rate. The model cannot distinguish parking duration
from active charging duration, so PARKING_TIME rates are not mislabeled as
charging-time rates. Conditional/time/session-dependent and unsupported
components stay unresolved. Conflicting structured prices are ambiguous.
Alternate human-readable tariff text is not used to override structured
amounts; one observed tariff description conflicted with its structured
ENERGY amount. Tariff `last_updated` values were timezone-less and are not
treated as status timestamps.

Locations are cached with a five-minute minimum refresh floor; the full tariff
list is cached for 15 minutes. These are CurbCharge safety/freshness policies,
not documented publisher quotas. Every refresh has a shared per-Home
Assistant lock, one-second inter-request pacing, a bounded timeout, a
16 MiB combined decompressed-body budget and a 20-page ceiling. New data is
committed atomically; failed or cancelled required fetches do not produce a
stale-success update.

## Sources

[open-data]: https://ev.vialietuva.lt/en/data-provision
[submission]: https://ev.vialietuva.lt/en/data-submission
[map]: https://ev.vialietuva.lt/en
[ocpi-tariffs]: https://ocpi.github.io/openapi-specification/ocpi/2.3.0/modules/tariffs/receiver-interface.html
[cc-by]: https://creativecommons.org/licenses/by/4.0/
[odc-by]: https://opendatacommons.org/licenses/by/1-0/
