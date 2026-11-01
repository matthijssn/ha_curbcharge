# Germany (Mobilithek / AFIR DATEX II) provider constraints

This research note records the source-specific constraints for the Germany
provider work tracked by [#313](https://github.com/matthijssn/ha_curbcharge/issues/313).
It is not a claim that Germany is release-ready — on the contrary, it
identifies structural blockers (see "Access model" and "Authentication
mechanism" below) that the original issue did not anticipate. Mobilithek was
checked on 2026-11-01 while logged in to a newly registered maintainer
account; recheck before provider release, since offers, schemas, and terms
are independently published per data provider and can change.

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
2. A separate **machine account with a client TLS certificate** (see
   "Authentication mechanism" below) — distinct from the organisation
   account itself.
3. An explicit **subscription request** submitted by the organisation's
   order manager.
4. **Manual approval by the data provider** (the CPO), not just by
   Mobilithek. The platform states: "The content data is only available to
   your organisation after it has been explicitly approved by the data
   provider... the data provider can also restrict the maximum number of
   downloads and a period during which downloads are possible."
5. Confirmed: "Individual data access via the portal allowed for anonymous
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

## Authentication mechanism (critical finding, corrects "unknown" below)

The Mobilithek portal UI does not document the actual consumer pull
mechanism; it only appears in Mobilithek's technical interface description
and in independent client implementations. A reviewed open-source Go client
([Karte-Bayern/Mobilithek](https://github.com/Karte-Bayern/Mobilithek),
MIT-licensed, written for a different Mobilithek dataset but against the
same subscription-pull infrastructure) confirms:

- **Authentication is mutual TLS (mTLS) with a client certificate**, not a
  bearer token or API-key header. Access requires a **machine account**
  that issues a PKCS#12 (`.p12`/`.pfx`) certificate bundle, converted to a
  PEM certificate + private key pair for the HTTP client. This is a
  distinct credential type from the organisation-account login used to
  browse and subscribe to offers in the portal.
- **Base URL**: `https://mobilithek.info:8443` — a dedicated port separate
  from the public portal (`:443`), used only for authenticated
  machine-to-machine subscription pulls.
- **Known endpoint shapes** (the reviewed client tries several, since the
  canonical shape is not uniformly documented across datasets):
  - `/mobilithek/api/V1.0/subscription?subscriptionID=<id>` (generic v1)
  - `/mobilithek/api/v1.0/subscription/<id>/clientPullService?subscriptionID=<id>` (DATEX II v2 client-pull)
  - `/mobilithek/api/v1.0/subscription/datexv3?subscriptionID=<id>` (DATEX II v3)
  - `/mobilithek/api/v1.0/container/subscription?subscriptionID=<id>` (container)
- The numeric subscription ID (not the public offer ID shown in the portal
  URL) identifies which approved subscription's content to pull.
- Response `Accept` is negotiated across `application/xml, text/xml,
  application/json, text/json` — some data is pulled as DATEX II **XML**
  even though the innocept mobility charging offer's portal metadata states
  "Syntax: JSON". Whether the AFIR charging profile specifically is served
  as JSON, XML, or either is **unverified** and must be confirmed with an
  actual approved subscription before implementation.
- Conditional requests (`ETag`/`If-Modified-Since` → HTTP `304`) are
  supported; `429`/`5xx` warrant backoff retries.

This materially changes the implementation picture versus every existing
CurbCharge provider (Belgium/NOBIL/AFDC all use a simple bearer token or
query-string API key): it requires a **new credential type** (client
certificate + private key, not a single string token) in the config flow,
secure storage of a private key in config-entry data, a dedicated
aiohttp/SSL client context per config entry (not the shared HA websession
every other provider injects), and likely its own machine-account
request/approval step with Mobilithek, separate from and in addition to the
per-CPO subscription approval above. None of this has been implemented
anywhere in CurbCharge before.

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
but **two compounding structural blockers** exist that the original issue
did not anticipate (it assumed a single national feed with a simple token,
akin to Finland's AFIR aggregator):

1. **Fragmented, per-CPO, provider-approved subscriptions** — no single
   feed covers all of Germany; a specific CPO must be chosen and that
   CPO must individually approve access (see "Access model" above).
2. **Mutual-TLS client-certificate authentication** — not a bearer
   token/API key like every existing CurbCharge provider, requiring a
   separate Mobilithek machine-account certificate, new config-flow
   credential handling (certificate + private key, not a single string),
   and a dedicated per-entry TLS client context (see "Authentication
   mechanism" above).

Before any implementation proceeds, two decisions are needed:

- **Which specific CPO(s) to target** for an initial "preview" (mirroring
  how Belgium's preview covers only Eco-Movement's own network) — this
  determines which company's approval must be obtained and how limited
  resulting coverage will be.
- **Whether to pursue mTLS client-certificate support at all** given it is
  a new credential type and security surface for the whole codebase, not
  an incremental change to an existing pattern. This is a maintainer
  product/architecture decision, not something to guess unilaterally.

No subscription or machine-account certificate has been requested as part
of this research; no real operator payloads have been captured. All schema
and endpoint findings above come from Mobilithek's publicly downloadable
JSON Schema files and a reviewed independent open-source client, not from
live content data or private correspondence.

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
- [Karte-Bayern/Mobilithek](https://github.com/Karte-Bayern/Mobilithek) — MIT-licensed
  independent Go client confirming the mTLS client-certificate authentication
  mechanism, base URL, and endpoint shapes (built for a different Mobilithek
  dataset, not AFIR charging specifically, but against the same subscription
  pull infrastructure)
