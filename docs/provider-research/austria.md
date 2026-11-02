# Austria (E-Control Ladestellenverzeichnis) provider eligibility

This note records the source assessment for
[#322](https://github.com/matthijssn/ha_curbcharge/issues/322). The outcome is
**technically eligible, paused pending a maintainer decision on the API terms
of use**; it is not a claim that Austria is release-ready. Sources and bounded
endpoint observations were checked on 2026-11-02; recheck them before any
implementation, since the API documentation is only available after
registration and the terms can change.

## Data source and legal basis

The [Ladestellenverzeichnis](https://www.ladestellen.at/) is Austria's
statutory national register of publicly accessible charging points, operated
by the regulator E-Control as an initiative of the Federal Ministry for
Economy, Energy and Tourism (BMWET). Under § 3(5) and § 4a of the federal
alternative-fuels infrastructure act and the
[Ladepunkt-Daten-VO (BGBl. II Nr. 257/2024)](https://www.ris.bka.gv.at/Dokumente/BgblAuth/BGBLA_2024_II_257/BGBLA_2024_II_257.html),
every operator of a publicly accessible charging point must report to
E-Control and keep current:

- static data per location and charging point (address and coordinates,
  connector count and type, maximum power in kW, AC/DC, authentication
  method, payment options, roaming, vehicle type), and
- dynamic data per charging point: operational readiness (in/out of
  service), **real-time availability (free/occupied)**, and the ad-hoc price.

Locations that charge free of charge are exempt from the dynamic-data
obligation, so their availability may legitimately be absent. The regulation
requires data to be kept "continuously current" (`laufend aktuell`) but
publishes no numeric refresh interval; do not present a cadence the source
does not state. E-Control's own
[register page](https://www.e-control.at/ladestellenverzeichnis) confirms that
per-point free/occupied status, ad-hoc prices, connector types and nominal
power are displayed.

Coverage is therefore national by statute and operator-reported. It is not an
independently audited completeness count, and E-Control's own disclaimer
notes that operators enter and maintain their own data.

## Third-party API and access model

E-Control offers a free, public API for third parties ("3. Nutzer-
Schnittstelle"). Registration is at
[admin.ladestellen.at/#/api/registrieren](https://admin.ladestellen.at/#/api/registrieren)
as either an organisation or a **private person**; the registrant accepts the
terms of use and receives an **individual API key** by e-mail plus access to
the online documentation. There is no client-certificate (mTLS) requirement.

This matches CurbCharge's credential policy: each end user registers and
obtains their own key, CurbCharge and its maintainers never request or
distribute keys, and any maintainer test key stays local and out of the
repository, CI, fixtures and documentation.

Two access characteristics matter for a Home Assistant integration:

- Requests carry the key in an `apikey` HTTP header. Requests without a valid
  key return HTTP `401` with a JSON error body.
- The registration form binds a key to one or more **domains that must match
  the `Referer` header** of each request. This is a web-application model; a
  server-side integration would have to send a `Referer` matching a domain
  the user registered. Whether E-Control accepts keys without domains, or how
  it expects non-browser clients to satisfy this, is not documented publicly.

The public website uses the same `/api/` paths with E-Control's own site key.
That key belongs to E-Control's website and **must never be reused or
embedded by CurbCharge**; the observations below were taken only to learn the
response shape and are not a usage pattern.

## Observed endpoint behaviour

Observed from the public website's own requests on 2026-11-02 (the
registered-user documentation was not consulted, so parameters and limits may
differ):

- `GET https://www.ladestellen.at/api/search?latitude=<lat>&longitude=<lon>`
  returned HTTP `200`, `application/json`, `Cache-Control: no-store`, and an
  array of 100 stations for central Vienna with straight-line distances from
  about 0.34 km to 1.2 km — i.e. a **bounded nearest-N search**, about
  130 KB. No radius or limit parameter is documented publicly.
- Each station carries `countryId`, `operatorId`, `stationId`,
  `stationStatus`, address and `location` (`lat`/`lon`), `distance` (km),
  operator/owner names, contact details, amenities, parking, opening hours and
  a `points` array.
- Each point (EVSE) carries `evseId` (format `AT*<operator>*<id>`),
  `capacityKw`, `connectorType` (code, description, `consumerName` such as
  `TYPE_2_AC`), `electricityType` (e.g. `AC_3_PHASE`), `authenticationMode`,
  payment providers, vehicle limits, `freeOfCharge`, `priceCentKwh`,
  `priceCentMin`, `startFeeCent`, blocking-fee fields and a live `status`.
  Observed status values: `AVAILABLE`, `CHARGING`, `BLOCKED`, `OUTOFORDER`,
  `INOPERATIVE`, `UNKNOWN` (OCPI-style). No per-point timestamp was observed;
  freshness would have to be treated as unknown unless the documentation
  defines one.
- `GET /api/point-data-export/region-codes` lists the nine Bundesländer; the
  website's "Ladepunktdaten exportieren" dialog offers a bulk export per
  Bundesland. This is a full-region snapshot, not a nearby search, and is
  not needed for CurbCharge's bounded model.
- A separate `/lade-tk/...` price endpoint returned `401` for anonymous use
  and is tied to the consumer login (ladetarif.at); it is out of scope.

## Terms of use (decisive for the gate)

The API terms are public at
[admin.ladestellen.at/#/api/terms-of-use](https://admin.ladestellen.at/#/api/terms-of-use)
and are accepted by the registrant. Points relevant to a user-hosted Home
Assistant integration:

- **Rate limits (§ 4):** at most 2,500 requests per hour per user; capacity
  for 30 concurrent requests is shared by all API users on a fair-use basis.
  E-Control may block access without notice when limits are exceeded. These
  are generous for one household but must be enforced as a hard ceiling.
- **Attribution (§ 3 c–e):** show the E-Control logo as an image link to
  `www.e-control.at`, show "Datenquelle: E-Control" directly next to the
  data, and (for publicly accessible applications) show E-Control's liability
  disclaimer text. Items c and d are implementable in entity attributes and
  the bundled card; item e applies to public applications.
- **Registrant duties (§ 3 a, b, f, g):** notify E-Control by e-mail when the
  service goes into production and when it is taken out of service (even
  temporarily), disclose the URL/IP of public internet applications, and
  **report access figures (unique visits/visitors) quarterly**. These duties
  are written for website operators and fall on each individual registrant,
  i.e. every CurbCharge user who obtains a key. CurbCharge cannot fulfil them
  on a user's behalf.
- **No alteration (§ 3 i):** transmitted data may not be changed in any way
  and must be displayed as delivered by the API. CurbCharge's existing
  normalisation (status enums, unit conversion such as cents to EUR,
  derived availability counts) is in tension with a literal reading. A
  faithful representation mapping is arguably not an alteration, but this is
  a maintainer judgement, not something to assume.
- **Credential secrecy (§ 3 j, § 8):** keys must not be passed to third
  parties and must be kept secret — compatible with per-user keys stored only
  in config-entry data and redacted from diagnostics.
- **Database right (§ 7):** E-Control holds the database copyright; copying
  all or part of the data is allowed only within statutory free-use
  exceptions, and passing data on as a file or re-serving it as a web service
  is prohibited. A user's own Home Assistant instance displaying data to that
  user is the intended use; recorder history should be kept to what Home
  Assistant needs.
- **Penalty (§ 9):** a strict-liability contractual penalty of **€10,000 per
  breach** of §§ 1–5, independent of fault, borne by the registrant.
- **Termination (§ 10):** E-Control may revoke access with three months'
  notice without cause, or immediately on suspected misuse.
- **Support (§ 5):** only basic documentation is provided; no developer
  support.

## Eligibility decision

**Technically eligible; implementation paused pending a maintainer decision.**

The source satisfies every technical gate CurbCharge applies: official
statutory national coverage, per-EVSE live availability, a bounded nearby
search, documented rate limits, free self-registered per-user keys and no
mTLS. The open questions are contractual, not technical:

1. Whether CurbCharge should build on terms that bind each end-user registrant
   to operator-style notification and quarterly reporting duties backed by a
   €10,000 strict-liability penalty.
2. Whether CurbCharge's normalisation is compatible with the "display as
   transmitted" clause, or whether the Austria provider would need to expose
   values verbatim.
3. How the domain-bound `Referer` requirement is meant to apply to
   server-side, non-browser clients.

Until these are resolved by the maintainer, keep
[#322](https://github.com/matthijssn/ha_curbcharge/issues/322) open and do
not add provider code. If implementation proceeds, the provider must enforce
the 2,500 requests/hour ceiling, send the user-registered `Referer`, render
the required attribution and disclaimer, keep the key out of logs and
diagnostics, and document the registrant's own duties in the user guide.

## Sources

- [Ladestellen.at (E-Control Ladestellenverzeichnis)](https://www.ladestellen.at/)
- [E-Control: Ladestellenverzeichnis overview](https://www.e-control.at/ladestellenverzeichnis)
- [Ladestellen admin portal: information for operators and API users](https://admin.ladestellen.at/#/informationen)
- [Ladestellen admin portal: API user registration](https://admin.ladestellen.at/#/api/registrieren)
- [Ladestellen admin portal: API terms of use](https://admin.ladestellen.at/#/api/terms-of-use)
- [Ladepunkt-Daten-VO, BGBl. II Nr. 257/2024](https://www.ris.bka.gv.at/Dokumente/BgblAuth/BGBLA_2024_II_257/BGBLA_2024_II_257.html)
- [Federal act on uniform standards for alternative-fuels infrastructure](https://www.ris.bka.gv.at/GeltendeFassung.wxe?Abfrage=Bundesnormen&Gesetzesnummer=20010261)
- [Regulation (EU) 2023/1804 (AFIR)](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32023R1804)
