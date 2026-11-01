# Germany (Mobilithek / AFIR DATEX II) provider constraints

This research note records the source-specific constraints for the Germany
provider work tracked by [#313](https://github.com/matthijssn/ha_curbcharge/issues/313).
It is not a claim that Germany is release-ready — on the contrary, it
identifies a structural blocker (see "Access model" below) that the original
issue did not anticipate. Mobilithek was checked on 2026-11-01 while logged
in to a newly registered maintainer account; recheck before provider release,
since offers, schemas, and terms are independently published per data
provider and can change.

## Platform

[Mobilithek](https://mobilithek.info/) is Germany's national AFIR Article 20
Access Point, operated by the Federal Ministry for Digital Affairs and
Transport (BMDV). It is a general-purpose mobility-data marketplace (13,931
data offers across all transport topics as of this check), not a
charging-specific service.

## Access model (critical finding)

**Mobilithek is not a single consolidated "all of Germany" charging feed.**
Unlike NDW DOT-NL, Belgium's Eco-Movement feed, or Finland's Digitraffic
AFIR API, Mobilithek is a marketplace where individual charge point
operators (CPOs) each independently publish their own AFIR Article 20 data
as separate "offers" — observed examples include INERGIA GmbH, innocept
mobility GmbH, Taubert Consulting GmbH, and GP JOULE Connect GmbH. Each CPO
publishes a paired static + dynamic offer (for example
`AFIR-recharging-stat-INERGIA` / `AFIR-recharging-dyn-INERGIA`).

Filtering by the "Filling and charging stations" category (which also
includes non-EV fuel stations) returned 91 total offers; a narrower search
for "Ladeinfrastruktur AFIR" matched 19 entries across several CPOs. There is
no evidence of one feed covering all German public chargers. A "geography:
Deutschland (DE)" tag on an offer means that specific operator's own network
happens to be distributed nationwide — not that the offer covers every
German charge point.

Access additionally requires, per offer:
1. A Mobilithek organisation account (free registration; already completed
   for this research).
2. An explicit **subscription request** submitted by the organisation's
   order manager.
3. **Manual approval by the data provider** (the CPO), not just by
   Mobilithek. The platform states: "The content data is only available to
   your organisation after it has been explicitly approved by the data
   provider... the data provider can also restrict the maximum number of
   downloads and a period during which downloads are possible."
4. Confirmed: "Individual data access via the portal allowed for anonymous
   users: No" and "Data access via the API is only allowed after approval of
   the subscription: Yes" (verified on the innocept mobility dynamic offer,
   ID `1026876702612758528`).

This is a materially heavier and slower access model than every other
CurbCharge provider: Belgium/NOBIL/AFDC require a single per-user API
key/token obtained once; Mobilithek requires a separate, provider-approved
subscription for every individual CPO whose coverage is wanted, with
turnaround time outside the maintainer's or CurbCharge's control. Reaching
useful multi-region coverage would mean requesting and waiting on approval
from several unrelated companies, not a one-time setup.

## License and fixtures

The reviewed offers are classified "Licence, free use/open data" with a
**Creative Commons Attribution 4.0 International (CC BY 4.0)** standard
licence, linked at
[dcat-ap.de/def/licenses/cc-by/4.0](http://dcat-ap.de/def/licenses/cc-by/4.0).
This matches the attribution approach already used for NOBIL. Test fixtures
should be synthetic, built from the published JSON Schema (see below)
rather than any real subscribed operator's data, both to avoid
redistributing real identifiers and because actual payloads cannot be
captured without an approved, provider-specific subscription.

Note: license terms are set per offer/provider on Mobilithek, not platform-
wide. Every additional CPO considered for support must have its own terms
re-verified; CC BY 4.0 should not be assumed for operators not directly
checked.

## Data model and schema (verified via official schema files)

Offers use **DATEX II V3** JSON under two officially versioned, named
profiles, both fetched directly from Mobilithek without authentication or an
active subscription (the metadata/schema is public; only the live content
data is gated):

- Static: `AFIR-Recharging-Static-01-00-00`
- Dynamic (delta): `AFIR-Recharging-Dynamic-01-00-00_Delta`

Schema files live at
`https://mobilithek.info/mdp-api/files/aux/schemas/DATEX_2_V3/<profile>/<file>.json`.
Confirmed files include `DATEXII_3_MessageContainer.json`,
`DATEXII_3_AfirFacilities.json`, `DATEXII_3_AfirEnergyInfrastructure.json`,
`DATEXII_3_Common.json`, `DATEXII_3_CommonExtension.json`,
`DATEXII_3_D2Payload.json`, and `DATEXII_3_ExchangeInformation.json`, one
set per profile.

### Structure

Records nest **Site → Station → RefillPoint** (an individual charging
point/connector). The dynamic profile mirrors this with
`EnergyInfrastructureSiteStatus` → `EnergyInfrastructureStationStatus` →
`RefillPointStatus`/`ElectricChargingPointStatus`. Every dynamic status
object carries a `reference` to its static counterpart via
`FacilityObjectVersionedReferenceG` — this is the stable join key between
the static and dynamic feeds (the same role NDW/Belgium/Finland's station
IDs play today).

### Dynamic (status) fields of note

- `status` — `RefillPointStatusEnumG` (connector-level status; exact enum
  values not yet enumerated — must be read from the schema before mapping
  to CurbCharge's `ConnectorStatus`).
- `isAvailable` (station level) — whether the *station* itself is available
  (not faulted/damaged/under maintenance). Explicitly documented as **not**
  indicating whether its connectors are currently occupied — must not be
  conflated with per-connector occupancy, consistent with CurbCharge's
  existing "don't infer occupancy from unrelated flags" rule (as already
  applied to NDW's `open` status).
- `currentChargingPower` (Watt), `currentVoltage` (Volt),
  `remainingChargingTime` (seconds), `nextAvailableChargingSlots`
  (array of timestamps), `waitingTime`.
- `energyRateUpdate` — dynamic ad-hoc pricing, including
  `EnergyBasedApplicability` (price validity by kWh range, e.g. "price is
  valid from X to Y kWh"). Confirms live/ad-hoc tariff data is modeled, not
  just static pricing.
- `fault` — fault/incident reporting at site, station, and refill-point
  level.

### Static (facility) fields of note

- `Connector.connectorType` — `ConnectorTypeEnumG` (not yet enumerated).
- `maxPowerAtSocket` (**Watt** — matches CurbCharge's existing normalized
  power unit), `voltage`, `maximumCurrent` (Ampere).
- `ChargingModeEnum` per IEC 61851-1: `mode1AC1p`, `mode1AC3p`, `mode2AC1p`,
  `mode2AC3p`, `mode3AC3p`, `mode4DC`, `legacyInductive`, `ccs`, `other`,
  `unknown`.
- `AuthenticationAndIdentificationEnum` (payment/access methods): `rfid`,
  `creditCard`, `debitCard`, `apps`, `nfc`, `cashPayment`,
  `unlimitedAccess`, `noAccess`, etc.
- `AccessibilityEnum`, `Amenities` (`illuminated`, `roofed`), and
  organisation/operator metadata (`AnOrganisation`: name, legal name,
  description, links).

This schema richness is comparable to or exceeds NDW/Belgium; the blocker is
entirely about *access to live content*, not about whether the data model
can represent what CurbCharge needs.

## Delivery and freshness

Observed on the INERGIA dynamic offer (one data point, not necessarily
representative of every CPO): delta push within about one minute of a status
change, full snapshot fallback at least every 6 hours. Its paired static
offer: push on change, full snapshot at least every 24 hours. The innocept
mobility dynamic offer documents "Support Delta Delivery: Yes" and
"Update Interval: On occurrence" without a specific numeric SLA. Freshness
and snapshot cadence are set per CPO, not platform-wide, and must be
re-verified for whichever specific operator(s) are eventually selected.

## Rate limits and quotas

**Unknown — not found during this review.** The offer metadata and Terms of
Use panel reviewed do not surface a rate-limit or quota field. Mobilithek
does note that "the data provider can also restrict the maximum number of
downloads and a period during which downloads are possible" per subscription,
implying limits are set per CPO/subscription rather than published platform-
wide. Must be confirmed once a specific subscription is approved, or by
asking Mobilithek/the CPO directly.

## Geographic coverage

Entirely dependent on which individual CPO offer(s) are subscribed to; see
"Access model" above. No single feed with full German national coverage was
found.

## Release status and open blocker

The schema is well-documented and license terms (CC BY 4.0) are favorable,
but **the fragmented, per-CPO, provider-approved subscription model is a
structural blocker that the original issue did not anticipate** (it assumed
a single national feed akin to Finland's AFIR aggregator). Before any
implementation proceeds, a scope decision is needed on **which specific
CPO(s) to target** for an initial "preview" (mirroring how Belgium's preview
covers only Eco-Movement's own network, not all Belgian chargers) — this is
a product decision, not something to guess unilaterally, since it determines
which company's approval the maintainer must obtain and how limited the
resulting coverage will be.

No subscription has been requested or approved as part of this research;
no real operator payloads have been captured. All schema findings above
come from Mobilithek's publicly downloadable JSON Schema files, not from
live content data.

## Sources

- [Mobilithek](https://mobilithek.info/)
- [innocept mobility GmbH dynamic offer (geography: Deutschland (DE))](https://mobilithek.info/offers/1026876702612758528)
- [innocept mobility GmbH static offer](https://mobilithek.info/offers/1019404392733597696)
- [INERGIA GmbH dynamic offer](https://mobilithek.info/offers/1043947032435105792)
- [INERGIA GmbH static offer](https://mobilithek.info/offers/1043944773714128896)
- Official DATEX II V3 schema files (fetched directly, no auth required):
  - [AFIR-Recharging-Static-01-00-00 / DATEXII_3_AfirFacilities.json](https://mobilithek.info/mdp-api/files/aux/schemas/DATEX_2_V3/AFIR-Recharging-Static-01-00-00/DATEXII_3_AfirFacilities.json)
  - [AFIR-Recharging-Static-01-00-00 / DATEXII_3_AfirEnergyInfrastructure.json](https://mobilithek.info/mdp-api/files/aux/schemas/DATEX_2_V3/AFIR-Recharging-Static-01-00-00/DATEXII_3_AfirEnergyInfrastructure.json)
  - [AFIR-Recharging-Dynamic-01-00-00_Delta / DATEXII_3_AfirEnergyInfrastructure.json](https://mobilithek.info/mdp-api/files/aux/schemas/DATEX_2_V3/AFIR-Recharging-Dynamic-01-00-00_Delta/DATEXII_3_AfirEnergyInfrastructure.json)
  - [AFIR-Recharging-Dynamic-01-00-00_Delta / DATEXII_3_AfirFacilities.json](https://mobilithek.info/mdp-api/files/aux/schemas/DATEX_2_V3/AFIR-Recharging-Dynamic-01-00-00_Delta/DATEXII_3_AfirFacilities.json)
- [Creative Commons Attribution 4.0 International legal code](https://creativecommons.org/licenses/by/4.0/legalcode)
