# NOBIL provider constraints

This research note records the source-specific constraints for the Norway and
Sweden provider work tracked by [#252](https://github.com/matthijssn/ha_curbcharge/issues/252)
and [#246](https://github.com/matthijssn/ha_curbcharge/issues/246). It is not a
claim that either country is release-ready. Sources were checked on
2026-10-26; recheck them before provider release.

## API use

The current NOBIL API v3 terms restrict direct API use in end-user apps and
services. They require the API to be used for internal processing and require
data to be processed, cached, or wrapped by an intermediate service before it
is made available to end users. A maintainer reports that NOBIL's written
clarification permits CurbCharge's user-hosted Home Assistant use when the
integration processes and caches the data server-side; the private
correspondence is not reproduced here.

The selected provider must keep requests on the Home Assistant backend, parse
and normalize responses before publishing entities, and never expose raw API
responses or credentials through entities, logs, or diagnostics. Each end user
supplies their own API key. A separate maintainer test key may be used for
local validation; no key belongs in the repository or CI.

## License and fixtures

NOBIL's API page and the Swedish Energy Agency's developer guidance identify
NOBIL data as CC BY 4.0. When sharing licensed material, preserve supplied
creator, attribution, copyright, and disclaimer notices; link to the source and
license; indicate modifications; and do not imply endorsement or add
restrictions. A suitable attribution should identify NOBIL and Enova, link to
`https://nobil.no` and CC BY 4.0, and say when CurbCharge has normalized or
otherwise modified the data. The license is separate from the API usage terms
above.

The Swedish Energy Agency's Trafficdata catalogue record currently lists
`license_id: notspecified` and `isopen: false`, despite the NOBIL API and
developer pages stating CC BY 4.0. Treat the direct NOBIL API license as
applying to NOBIL API data, but do not infer the license for any separate
catalogue resource from it. Prefer synthetic fixtures based on the documented
API schema; if NOBIL material is used, retain the required attribution and
license link.

## Request limits and retries

The current public API v3 documentation does not publish a requests-per-time
quota, reset period, or recommended polling interval. The documented nearby
station search returns at most 2,000 station records per response; this cap is
not a request-rate quota. Do not invent a time-based quota or polling interval
when the source does not specify one.

The selected country-specific provider must enforce the published response
cap, honor server-issued `429` and `Retry-After` responses, use bounded backoff
for transient failures, and avoid rapid retry loops for authentication or
invalid-payload errors. Tests should verify these explicit behaviors without
asserting an undocumented requests-per-time budget.

## Country coverage and freshness

NOBIL describes its database as covering most public charging stations in
both Norway and Sweden and documents country codes `NOR` and `SWE`. The public
documentation does not provide numeric country-level counts. Numeric totals
are not a requirement when they are not published. The API documentation
lists country-specific statistics functions, including
`stats_GetSumChargerstations`, `stats_GetCountChargerConnectors`, and
`stats_GetCountPublicChargerConnectors`, which accept `countrycode=NOR` or
`countrycode=SWE`; these document query methods, not required numeric results.
The Swedish catalogue lists a daily frequency; this is catalogue metadata,
not a guarantee that every NOBIL field or status is refreshed daily.

NOBIL includes availability only where it is reported. Its real-time status
feed is a separate Enova WebSocket product, and the documented test
environment has no status data. Unless that feed is separately validated and
approved, availability not present in the selected provider response must
remain unknown. Tariffs must likewise remain unknown until their fields and
semantics are verified.

## Release status

The source-specific conditions above must be implemented and tested in the
Norway and Sweden providers, not in a new generic provider framework. Numeric
per-time quotas and country totals that NOBIL does not publish are not
requirements. The providers must honor the published response cap and server
rate-limit responses while preserving unknown data semantics.

## Sources

- [NOBIL API page: documentation, usage terms, and license](https://info.nobil.no/api)
- [NOBIL API v3 documentation, revision 2026-08-16](https://info.nobil.no/images/API_NOBIL_Documentation_v3_20260816.pdf)
- [Creative Commons Attribution 4.0 International legal code](https://creativecommons.org/licenses/by/4.0/legalcode)
- [Swedish Energy Agency developer guidance](https://www.energimyndigheten.se/klimat/transporter/laddinfrastruktur/registrera-din-laddstation/utvecklare/)
- [Swedish Energy Agency Trafficdata catalogue record](https://trafficdata.se/api/3/action/package_show?id=charging-points-for-electric-vehicles-nobil)
