# Finland (Fintraffic/Digitraffic AFIR) provider constraints

This research note records the source-specific constraints for the Finland
provider work tracked by [#289](https://github.com/matthijssn/ha_curbcharge/issues/289)
and [#288](https://github.com/matthijssn/ha_curbcharge/issues/288). It is not a
claim that Finland is release-ready. The REST contract and sample responses
were checked on 2026-10-29 against the production API and its published
OpenAPI document; recheck both before provider release, since the OpenAPI
`info.version` is a timestamp that changes whenever the schema is updated.

## API access and use

The AFIR REST API (`https://afir.digitraffic.fi`) is open data with no API key,
token, or registration: its OpenAPI document defines no security schemes, and
anonymous `GET` requests succeed. Digitraffic asks (not requires) every caller
to send a `Digitraffic-User` header identifying the application (for example
`Digitraffic-User: ha_curbcharge/0.x`) so Fintraffic can contact an operator
about excessive load; this header must never contain personal information.
Compression is mandatory: requests must send `Accept-Encoding: gzip` or the
service returns HTTP `406`. There is no end-user credential to collect in the
config flow for this source.

## License and fixtures

Digitraffic's open data, including AFIR, is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Fintraffic's terms
of service require preserving the material's copyright/version/publisher
notice and any disclaimers, linking to the original source where technically
possible, stating what was modified, and including a visible indication of the
CC BY 4.0 license with a link to it. The documented attribution example is
`Source: Fintraffic / digitraffic.fi, license CC 4.0 BY`. Test fixtures should
be synthetic, built from the documented/observed schema below rather than
verbatim captures of live operator data, to avoid redistributing real
operator/location identifiers as if they were canonical examples.

## Request limits and retries

Digitraffic's general API guidance documents a default restriction of 60
requests per minute per IP address without the `Digitraffic-User` header
(higher limits are available with it, but no specific numeric alternative is
published). Exceeding the limit returns HTTP `429`. The AFIR OpenAPI document
also defines a generic `500` response for all endpoints. Most Digitraffic
responses are cached for about one minute, so polling faster than that does
not yield newer data.

The provider must send `Accept-Encoding: gzip` and a `Digitraffic-User`
header, honor `429` with bounded backoff (the AFIR docs do not publish a
`Retry-After` header or a per-time quota beyond the 60-requests/minute
guidance above), and avoid tight retry loops on `406`/`5xx` or malformed
payloads.

## Data schema and join keys

All AFIR endpoints are unauthenticated `GET` requests under
`/api/charging-network/v1/`:

- `/operators` — operator `id` (`<countryCode>*<partyId>`, e.g. `FI*911`),
  `partyId`, `countryCode`, and `details.name`/`website`/`logo`.
- `/locations` (GeoJSON `FeatureCollection`) — each feature's `properties`
  holds a location `id`, `name`, `address`, `operator` (embedded, matching
  `/operators`), `openingTimes`, `chargingWhenClosed`, `createdAt`/
  `modifiedAt`, and an `evses` array. Each EVSE has a stable `id` in the
  format `<countryCode(2)>*<partyId(3)>*<id_string(1-30)>` (observed example:
  `FI*911*E*PDC*FI1000244*0000001`) and a `connectors` array with `standard`
  (e.g. `IEC_62196_T2`), `format` (`CABLE`/`SOCKET`), `powerType`
  (`AC_1_PHASE`/`AC_3_PHASE`/`DC`), `maxVoltage`/`maxAmperage`/
  `maxElectricPower` (watts), and a `tariffIds` array.
- `/locations/statuses` — a flat `statuses` array keyed by the same `evseId`,
  each with `status` (`AVAILABLE`, `BLOCKED`, `CHARGING`, `INOPERATIVE`,
  `OUTOFORDER`, `PLANNED`, `REMOVED`, `RESERVED`, `UNKNOWN`), `modifiedAt`, and
  `lastUpdatedAt`. Joining statuses to connectors must go through `evseId`,
  not location `id`.
- `/tariffs` — a `tariffs` array keyed by `id` (matched via connector
  `tariffIds`), with `currency`, `type`, `taxIncluded`
  (`YES`/`NO`/`N/A`), and OCPI-style `elements[].priceComponents[]` entries
  (`type` one of `ENERGY`/`FLAT`/`PARKING_TIME`/`TIME`/`CONGESTION_TIME`,
  `price`, `stepSize`, optional `vat`). This shape is structurally close to
  the OCPI tariff fields CurbCharge already parses for Belgium.
- Datex II v3.6/v3.7 variants of locations/statuses/tariffs exist for the
  same data; the JSON/GeoJSON endpoints above are sufficient and simpler to
  map into CurbCharge's normalized models.

GeoJSON `/locations` and `/locations/statuses` support `operatorCountryCode`,
`operatorPartyId`, `evseId`, `powerType`, `connectorType`, `connectorFormat`,
`minPower`/`maxPower`, and `evseStatus` query filters.
`/locations/statuses` additionally supports `modifiedAfter`/
`modifiedBeforeOrAt` for incremental polling.

**The dataset is not Finland-only.** A live unfiltered sample of
`/locations` returned a mix of `operator.countryCode` values including `FI`
and `NO`. The Finland provider must always send `operatorCountryCode=FI` (and
use the `FI` country code consistently for joins) rather than assuming an
unfiltered request already scopes to Finland.

## Pagination and snapshots

Pagination is cursor-based with a currently fixed page size: `limit` must be
exactly `500` or `ALL` (any other numeric value is rejected with a `400`
validation error), and a response's `pagination.nextCursor` is used as the
next request's `cursor` query parameter; a missing/`null` `nextCursor` means
no further pages. `limit=ALL` (or the `/all` snapshot path) returns the full
generated-every-minute snapshot via redirect. A live `operatorCountryCode=FI`
sample returned exactly 500 locations with a non-null `nextCursor`, so Finland
alone exceeds one page; the provider must follow `nextCursor` (or use the
snapshot endpoint) rather than assuming a single page covers Finland. Page
size and snapshot cadence are operational details Digitraffic may change; do
not treat either as a request-rate quota or as proof that every operator's
underlying status is refreshed within that interval.

## Freshness

The feature collection and each status entry expose both `modifiedAt`
(record-level change time) and `lastUpdatedAt` on statuses (observed to run a
few hours behind `modifiedAt` in sampled data) — these are not guaranteed to
be identical, so timestamp handling must not assume they always match.
Timestamps are ISO 8601 UTC (`Z`-suffixed). Snapshot generation every minute
and ~1 minute response caching describe service-side behavior, not a
guarantee that every CPO pushes real-time updates at that cadence.

## Real-time (MQTT) and availability

A separate anonymous MQTT/WebSocket interface
(`wss://afir.digitraffic.fi:443/mqtt`) publishes the same status payloads in
near real time, topic-scoped by
`status-v1/<operatorCountryCode>/<operatorPartyId>/<locationId>/<evseId>`.
This is out of scope for the initial REST-based provider; if added later it
must be validated and tested separately. Until then, availability comes only
from the polled `/locations/statuses` REST response, and any EVSE absent from
that response must remain unknown rather than inferred.

## Release status

The constraints above must be implemented and tested in the Finland provider,
not in a new generic provider framework. No numeric total of Finnish charging
points is published by Digitraffic; the sampled 500+ FI locations in one
unfiltered page is observational evidence of scale, not a documented total,
and must not be presented as a coverage guarantee. The provider must honor the
documented pagination contract (or validated snapshot access), the `Digitraffic-User`/
gzip conventions, and the 60-requests-per-minute-per-IP guidance with bounded
retry/backoff on `429`/`5xx`/malformed responses.

## Sources

- [Digitraffic AFIR API overview](https://www.digitraffic.fi/en/road-traffic/afir/)
- [AFIR OpenAPI document](https://afir.digitraffic.fi/swagger/openapi.json) and [Swagger UI](https://afir.digitraffic.fi/swagger/)
- [Digitraffic road traffic general info and restrictions](https://www.digitraffic.fi/en/road-traffic/)
- [Digitraffic API usage instructions (headers, compression, rate limiting)](https://www.digitraffic.fi/en/support/instructions/)
- [Digitraffic terms of service (CC BY 4.0 attribution requirements)](https://www.digitraffic.fi/en/terms-of-service/)
- [Creative Commons Attribution 4.0 International legal code](https://creativecommons.org/licenses/by/4.0/legalcode)
