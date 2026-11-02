# Germany (Mobilithek / AFIR DATEX II) provider constraints

This research note records the source-specific constraints for the Germany
provider work tracked by [#313](https://github.com/matthijssn/ha_curbcharge/issues/313).
It is not a claim that Germany is release-ready — on the contrary, it
identifies structural blockers (see "Access model" below; the
authentication mechanism itself is now confirmed feasible, see
"Implementing mTLS client certificates inside Home Assistant" below) that
the original issue did not anticipate. Mobilithek was checked on
2026-11-01 while logged in to a newly registered maintainer account;
recheck before provider release, since offers, schemas, and terms are
independently published per data provider and can change.

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

## Implementing mTLS client certificates inside Home Assistant (feasibility)

This is feasible and has a direct, already-shipped precedent in Home
Assistant core itself: **the built-in MQTT integration already supports
client-certificate (mTLS) authentication**, confirmed by reading its source
(`homeassistant/components/mqtt/util.py`, `client.py`, `config_flow.py` on
the `dev` branch). The pattern below is not speculative; it is what HA core
actually does today.

### Collecting the certificate and key

HA's MQTT config flow lets a user paste **unencrypted PEM content directly**
into multiline text fields for the client certificate and client private
key (not just a file path) — the raw PEM string is what gets stored in
config-entry data. A known format quirk (documented in
[home-assistant/core#142646](https://github.com/home-assistant/core/issues/142646)):
EC (elliptic-curve) private keys in `-----BEGIN EC PRIVATE KEY-----` form
may need conversion to PKCS#8 (`-----BEGIN PRIVATE KEY-----`, via
`openssl pkcs8 -topk8 -nocrypt`) before HA accepts them; RSA keys in
`-----BEGIN PRIVATE KEY-----`/`-----BEGIN RSA PRIVATE KEY-----` form work
directly. This matters because Mobilithek issues machine-account
certificates as a `.p12`/`.pfx` bundle; the key type it contains (RSA vs
EC) is unverified and affects whether this conversion step is needed.

### Turning PEM content into a usable `ssl.SSLContext`

Python's stdlib `ssl.SSLContext.load_cert_chain()` only accepts **file
paths**, not in-memory PEM strings (confirmed: still true as of current
CPython; long-standing stdlib limitation, see
[cpython#60691](https://github.com/python/cpython/issues/60691)). HA core's
MQTT integration bridges this exact gap with a confirmed, reusable pattern
in `mqtt/util.py`:

1. `async_create_certificate_temp_files(hass, config)` runs in the executor
   (`hass.async_add_executor_job`, off the event loop, since file I/O is
   blocking) and writes each PEM string to a file inside a dedicated temp
   directory created with **`mkdir(0o700)`** (owner-only permissions).
2. `get_file_path(option, default)` returns the resulting file path, which
   is passed to the TLS client as `certfile`/`keyfile` — exactly what
   `ssl.SSLContext.load_cert_chain(certfile, keyfile)` requires.

A Germany provider would need its own equivalent of this (not importing
MQTT's internal `util.py`, which is private to that integration and not a
public `homeassistant.helpers` API — reimplementing the same ~15-line
pattern locally in `germany.py`/`config_flow.py` is correct and matches how
every other CurbCharge provider keeps its logic self-contained).

### Why the shared HA aiohttp session cannot be used

Every existing CurbCharge provider uses
`homeassistant.helpers.aiohttp_client.async_create_clientsession(hass, ...)`,
which only exposes a boolean `verify_ssl` toggle — its connector is always
built internally (`_async_get_connector`) and **cannot accept a custom
`ssl.SSLContext` or client certificate**. This was confirmed by reading the
helper's source
(`homeassistant/helpers/aiohttp_client.py` on `dev`). A Germany provider
must therefore create and manage **its own** `aiohttp.ClientSession` with
`aiohttp.TCPConnector(ssl=ssl_context)`, mirroring the shape of:

```python
ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ssl_context.load_cert_chain(certfile=cert_path, keyfile=key_path)
connector = aiohttp.TCPConnector(ssl=ssl_context)
session = aiohttp.ClientSession(connector=connector)
```

This is a deliberate, justified deviation from CurbCharge's
`inject_websession` quality-scale pattern (currently `done` for every other
provider, all of which share the HA-managed session) — not a violation of
it, but it must be called out explicitly in code and documentation, and the
provider must own the session's full lifecycle: create it in
`async_setup_entry`, close it in `async_unload_entry` (HA's shared-session
auto-cleanup does not apply to a self-managed session).

### Diagnostics and storage

Both the client certificate and especially the private key must be
redacted in diagnostics output, following the same principle already
applied to the Belgium/NOBIL/AFDC tokens. Config-entry data is not
separately encrypted at rest regardless of value shape, so storing a PEM
private key there carries the same risk profile as storing any other
provider secret CurbCharge already handles — the new risk is the
temp-file-on-disk step above, mitigated the same way HA core does it
(`0o700` directory permissions, executor-job writes, cleanup on unload).

### Conclusion

Implementing mTLS is technically feasible in Home Assistant and has a
direct, working precedent to copy the shape of (not guess at) in HA core's
own MQTT integration. It is still a materially larger implementation than
any existing CurbCharge provider: a new credential type in the config flow,
a self-managed TLS session with its own lifecycle, and (per the "Access
model" section above) a Mobilithek machine-account certificate that must
still be obtained, separately from the per-CPO subscription approval.

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

**Blocker 2 is now resolved to "feasible, with a known implementation
shape"** — see "Implementing mTLS client certificates inside Home
Assistant (feasibility)" above, which reuses a pattern already shipped in
HA core's own MQTT integration. It remains a larger lift than any existing
CurbCharge provider (new credential type, self-managed TLS session), but is
no longer an open technical unknown.

Before any implementation proceeds, one decision remains:

- **Which specific CPO(s) to target** for an initial "preview" (mirroring
  how Belgium's preview covers only Eco-Movement's own network) — this
  determines which company's approval must be obtained and how limited
  resulting coverage will be. A separate Mobilithek machine-account
  certificate must still be obtained regardless of which CPO is chosen.

No subscription or machine-account certificate has been requested as part
of this research; no real operator payloads have been captured. All schema
and endpoint findings above come from Mobilithek's publicly downloadable
JSON Schema files, a reviewed independent open-source client, and Home
Assistant's own core source code — not from live content data or private
correspondence.

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
- Home Assistant core source (`dev` branch), confirming the HA-side
  implementation pattern for client-certificate auth:
  - [`homeassistant/components/mqtt/util.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/mqtt/util.py) —
    `async_create_certificate_temp_files`/`get_file_path` (PEM content to
    temp file to file path)
  - [`homeassistant/components/mqtt/client.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/components/mqtt/client.py) —
    `tls_set(certfile=client_cert, keyfile=client_key, ...)` usage
  - [`homeassistant/helpers/aiohttp_client.py`](https://github.com/home-assistant/core/blob/dev/homeassistant/helpers/aiohttp_client.py) —
    confirms `async_create_clientsession` has no connector/SSL-context
    override, only a `verify_ssl` boolean
  - [home-assistant/core#142646](https://github.com/home-assistant/core/issues/142646) —
    documents the EC-private-key PKCS#8 conversion quirk in HA's
    certificate text fields
  - [cpython#60691](https://github.com/python/cpython/issues/60691) —
    confirms `ssl.SSLContext.load_cert_chain` has no in-memory/string
    alternative to file paths in the stdlib
