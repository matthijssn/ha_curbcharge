# Portugal (NAP/MOBI.E) provider eligibility

This note records the source assessment for [#319](https://github.com/matthijssn/ha_curbcharge/issues/319) and the implementation gate in [#320](https://github.com/matthijssn/ha_curbcharge/issues/320). It is a **no-go under the currently documented public feeds**, not a claim that Portugal is release-ready. Sources and bounded endpoint probes were checked on 2026-11-02; recheck if MOBI.E or the Portuguese NAP publishes consumer terms or a more bounded interface.

## API access and data source

The [Portugal NAP charging-data listing](https://www.nap-portugal.imt-ip.pt/nap/multimodalsupply) lists separate static and dynamic resources for the Portuguese charging network and identifies DATEX II as the data model. It links directly to the [static feed](https://ev-nap.mobie.pt/integration/nap/evChargingInfra) and [dynamic status feed](https://ev-nap.mobie.pt/integration/nap/evActualStatus).

An unauthenticated request to the dynamic feed returned DATEX II XML with an `EnergyInfrastructureStatusPublication`, a `publicationTime`, publisher country `PT`, and connector-level statuses. No client certificate, token, or API key was needed for that request.

MOBI.E's [MOBI.Data information](https://www.mobie.pt/en/mobility/mobi.data) distinguishes data for the total national network that MOBI.E submits to the NAP from data collected for operators connected to the MOBI.E management platform. The NAP listing describes its feeds as the Portuguese MOBI.E charging network, but the reviewed material does not establish whether these endpoints cover all public chargers or only the connected network.

MOBI.E Technical Rule no. 1/EADME/2026 describes how charging point operators provide data **to** the EADME: DATEX II XML links or OCPI. Its unauthenticated-link requirement applies to those operator-to-EADME links; it does not establish third-party consumer reuse rights for the NAP feed.

## Licensing and reuse

The reviewed NAP listing, MOBI.Data page, and technical rule do not state a license, attribution requirement, or consumer-facing terms for reusing the consolidated feeds. The technical rule describes the operators' reporting obligations, not an end-user integration license. Do not infer permission for CurbCharge from the feed's public accessibility alone; obtain authoritative reuse terms before implementing a provider.

## Freshness and request behavior

MOBI.E's technical rule requires operators to update static data after a change and no later than 24 hours after it, and dynamic data after a change and no later than one minute after it. These are operator-to-EADME reporting requirements, not a measured freshness guarantee or polling interval for the NAP feeds.

Bounded probes of the unfiltered dynamic feed observed:

- HTTP `200` with `Content-Type: application/xml` and a response size of about 38 MB.
- A new `ETag` and HTTP `200` on conditional `If-None-Match` HEAD probes about three minutes apart; a `304` response was not observed.
- No `Content-Encoding: gzip` in response to a request with `Accept-Encoding: gzip`.

A HEAD request to the static feed timed out after 30 seconds with no response. The public NAP listing documents one direct URL for each feed, but no geographic filters, pagination, delta endpoint, request quota, or consumer polling guidance was found. The observed unfiltered dynamic response and unverified conditional behavior do not establish a bounded refresh strategy for Home Assistant.

## Schema and data semantics

The dynamic sample uses DATEX II model base version 3 and contains facility references, refill-point statuses, and pricing-related overrides. The static feed could not be retrieved in the bounded probe, so static-field mapping and the static-to-dynamic join keys remain unverified. Price fields must remain unknown until their semantics and reuse conditions are documented. Do not commit live station payloads as fixtures; any future tests should use synthetic DATEX II examples.

## Coverage and transition status

The NAP resource names alone do not prove complete countrywide coverage. MOBI.E's technical rule describes a transition through 2026-12-31 during which data from operators already connected to the existing platform is transformed by the EADME into DATEX II; other operators must use the defined reporting methods. The source material reviewed does not provide a completeness statement or a countrywide station count for the two NAP feed URLs.

## Eligibility decision

**No-go for provider implementation under the current evidence.** The direct feed is reachable without credentials and contains live-status fields, but:

1. Reuse rights and consumer terms are not established.
2. The exact geographic coverage is not established.
3. The dynamic endpoint returns an unfiltered response of about 38 MB, and the reviewed documentation does not describe filters, deltas, or an effective conditional-polling contract. Compression was not observed, and the static feed did not respond within the bounded probe.

Keep [#320](https://github.com/matthijssn/ha_curbcharge/issues/320) blocked. Reconsider Portugal only after authoritative confirmation of reuse terms and coverage, plus documentation or tests showing that nearby-station refresh can be bounded without repeatedly downloading the full feed. Otherwise, re-screen other countries.

## Sources

- [Portugal NAP charging-data listing](https://www.nap-portugal.imt-ip.pt/nap/multimodalsupply)
- [MOBI.E data aggregation and NAP information](https://www.mobie.pt/en/mobility/mobi.data)
- [MOBI.E Technical Rule no. 1/EADME/2026 (Portuguese PDF)](https://www.mobie.pt/documents/699315/699782/Regra+T%C3%A9cnica+1_EADME_18052026.pdf/28f8cc57-7748-ef63-099b-840de7103d5f?t=1779111921920)
- [Static DATEX II feed](https://ev-nap.mobie.pt/integration/nap/evChargingInfra)
- [Dynamic DATEX II status feed](https://ev-nap.mobie.pt/integration/nap/evActualStatus)
