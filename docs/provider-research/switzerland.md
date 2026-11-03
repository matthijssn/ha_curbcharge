# Switzerland (SFOE ich-tanke-strom OCPI) provider constraints

This research note records the source-specific constraints for the Switzerland
provider work tracked by [#324](https://github.com/matthijssn/ha_curbcharge/issues/324).
Switzerland ships as a **preview** because the source itself is in a
transition period (see "Coverage"). The API contract and live measurements
were checked on 2026-11-03 against the production API and the SFOE
documentation; recheck both before promoting the provider out of preview,
since SFOE states the final rate limit and the December 2026 go-live details
may still change.

## Source and legal basis

[ich-tanke-strom.ch](https://www.ich-tanke-strom.ch) is Switzerland's national
data infrastructure for electric mobility, operated by the Swiss Federal Office
of Energy (SFOE/BFE). It aggregates charging-point operator (CPO) data in real
time and publishes it as open data. The documentation lives in the
[SFOE/ichtankestrom_Documentation](https://github.com/SFOE/ichtankestrom_Documentation)
repository.

## API access and use

The new ich-tanke-strom.ch REST API
(`https://api.emobility-prod.prometheon.bfe.admin.ch/ocpi/`) is public,
read-only, and requires **no authentication** — no API key, token,
registration or `Authorization` header, and no client certificate. The
documentation says the new API "applies from December 2026", but live requests
on 2026-11-03 already returned production data with OCPI `status_code` 1000.
The provider therefore targets this API directly. The legacy OICP JSON files,
multilingual GeoJSON files and the GeoServer WFS "Feature-API" are not used:
the OICP data file is a 28.8 MB full-country snapshot (unbounded) and that
interface is being retired.

There is no end-user credential to collect in the config flow. Requests send
`Accept: application/json` and a `User-Agent: ha_curbcharge` header.

## License and attribution

SFOE publishes the dataset under opendata.swiss terms: "Open use. Must provide
the source. Use for commercial purposes requires permission of the data
owner." The documentation spells this out as:

- non-commercial use is permitted;
- commercial use requires prior permission from SFOE
  (`ich-tanke-strom@bfe.admin.ch`);
- the source must be provided (author, title and link to the dataset).

CurbCharge is non-commercial open-source software. The user guide states this
and tells commercial deployments to obtain permission separately. The
attribution used is: Swiss Federal Office of Energy SFOE, ich-tanke-strom.ch,
dataset "Ladestationen für Elektroautos" on opendata.swiss, normalized by
CurbCharge, no endorsement implied. Test fixtures are synthetic, built from
the documented OCPI schema; no live operator payload is committed.

## Request limits and retries

The documentation gives an initial technical limit "for example 60 requests
per minute per client", answered with HTTP `429` and a `Retry-After` header,
and says the final value will be set before go-live. No per-time quota beyond
that is published. The provider paces requests at about 1.1 s through a
limiter shared by all Swiss entries in one Home Assistant instance, honours
`429` + `Retry-After` by pausing that shared limiter, treats `5xx` as a
transient connection error, and raises an invalid-response error for a
non-`1000` OCPI `status_code` or malformed JSON. Do not assert a numeric
quota the source does not publish.

## Data model and join keys

Responses are OCPI 2.2.1 / 2.3.0 JSON wrapped in the OCPI envelope
`{data, status_code, status_message, timestamp}`.

- `GET /locations` — `country_code`, `party_id`, `id`, `name`, `address`,
  `city`, `postal_code`, `country` (ISO 3166-1 alpha-3, e.g. `CHE`),
  `coordinates` (`latitude`/`longitude` as strings), `operator.name`,
  `evses[]` (`uid`, `evse_id`, `status`, `physical_reference`,
  `connectors[]`, `last_updated`), `last_updated`. Connectors carry
  `standard` (OCPI, e.g. `IEC_62196_T2`), `format`, `power_type`,
  `max_electric_power` in **watts**, and `tariff_ids`.
- `GET /tariffs` — `country_code`, `party_id`, `id`, `currency` (observed:
  `CHF` only), `tax_included` (observed: `NO`), `elements[].price_components[]`
  (`type` in `ENERGY`/`TIME`/`FLAT`/`PARKING_TIME`, `price`, `step_size`,
  `vat`; observed VAT 8.1 %) with `restrictions` (`day_of_week`,
  `start_time`, `end_time`, `min_duration`, `max_duration`). This is the same
  OCPI shape the Finland provider parses; time-of-day and duration
  restrictions are treated as conditional and the price is not guessed.
- `GET /status?date_from=<UTC>&limit=<n>` — flat records keyed by
  `country_code`, `party_id`, `location_id`, `evse_uid`, with `status` and
  `last_updated`. **This is the only source of live availability.** The
  `evses[].status` inside `/locations` refreshes only when the location record
  is re-published ("several times a day") and must not be used as
  availability.

The join key between catalogue and status is
`(country_code, party_id, location_id, evse_uid)`, matching the location's
`country_code`/`party_id`/`id` and the EVSE `uid`. One CurbCharge station is
created per EVSE with `provider_id = "{country_code}*{party_id}*{location_id}*{evse_uid}"`
and `evse_id` as the displayed identifier when present. Observed status values:
`AVAILABLE`, `CHARGING`, `INOPERATIVE`, `OUTOFORDER`, `UNKNOWN`; the OCPI
2.2.1 set (`BLOCKED`, `PLANNED`, `REMOVED`, `RESERVED`) is mapped as well and
`REMOVED` EVSEs are excluded.

**The dataset is not CH-only by operator.** A live record had
`country_code: "FR"`, `party_id: "073"` with `country: "CHE"`, Swiss
coordinates and `physical_reference: "CH*SCH*…"`. The provider filters on the
**location's** `country == "CHE"` and never on the operator `country_code`,
so foreign-operated Swiss sites are kept. One `LIE` (Liechtenstein) location
was observed and is correctly excluded.

Address fields are unreliable in live data (tab characters, city and postal
code swapped, `time_zone: Europe/London` on a Swiss site). Identity and
distance use IDs and coordinates only; address text is whitespace-normalised
and display-only.

## Pagination and measured sizes (2026-11-03)

Lists are paginated OCPI-style: `limit` (1–1000, `/status` 1–5000) and
`offset`, with a `Link: <next-url>; rel="next"` response header and
`X-Limit`/`X-Total-Count` headers. The last page has no `Link` header. The
provider follows `Link`, requires the next URL to stay on the API origin, and
stops on a repeated URL or after 50 pages.

| Endpoint | Pages | Records | Bytes | Notes |
| --- | --- | --- | --- | --- |
| `/locations?limit=1000` | 2 | 1,210 locations / 4,316 EVSEs | 3.3 MB | 1,209 `CHE`, 1 `LIE`; no gzip |
| `/tariffs?limit=1000` | 3 | 2,621 | ~2.3 MB | all `CHF`, `tax_included: NO` |
| `/status?date_from=2000-01-01…&limit=5000` | 1 | 3,634 | 514 KB | full current set; ~680 EVSEs have no record |
| `/status?date_from=<30 min ago>` | 1 | 516 | — | typical delta |

A first poll (catalogue + tariffs + full status) took about 7 s with 1.1 s
pacing; a delta poll about 0.5 s. Locations and tariffs are cached for
15 minutes per provider instance and re-fetched in full on expiry (two and
three pages respectively, so delta sync of the catalogue is not needed).
Status records are merged per poll with `date_from = last poll − 5 min`; a
record is ignored if it is older than the one already held. EVSEs with no
status record stay **unknown**; on 2026-11-03 that was about 22 % of EVSEs.
About 65 % of EVSEs referenced a `tariff_ids` entry that `/tariffs` does not
publish; these report a *missing* tariff rather than a guessed price.

## Coverage (why this is a preview)

The legacy OICP status feed listed **21,034 EVSEs across 41 operators** on the
same day, while the new OCPI API exposed **4,316 EVSEs (about 20 %)**.
Operators are migrating to the new platform ahead of the December 2026
switch, so coverage is incomplete and growing. Switzerland is therefore
labelled "(preview)" like Belgium: an empty nearby result may mean the local
operator has not migrated yet. Re-measure before dropping the preview label.

## Freshness

Status records carry `last_updated` (the change time at the CPO). Locations
and tariffs refresh several times a day; `last_updated` is the CPO change
time, and the docs recommend a 24–48 h overlap when synchronising by
`date_from`. These describe the aggregator's behaviour, not a guarantee that
every CPO pushes updates at a given cadence. When a status record exists its
`last_updated` is exposed as the station's dynamic timestamp; otherwise the
EVSE's static `last_updated` is used as a fallback and availability is
unknown.

## Release status

The constraints above are implemented and tested in the Switzerland provider,
not in a shared OCPI framework. The provider honours the documented
pagination contract, the 60-requests-per-minute guidance with bounded
retry/backoff on `429`/`5xx`/malformed responses, the CHE location filter,
and unknown-data semantics for missing statuses and unpublished tariffs. The
preview label stays until coverage approaches the legacy feed and SFOE has
fixed the final rate limit.

## Sources

- [SFOE documentation index](https://github.com/SFOE/ichtankestrom_Documentation)
- [Access the data: terms, GeoJSON, API overview](https://github.com/SFOE/ichtankestrom_Documentation/blob/main/How%20to%20access%20the%20data.md)
- [How to query the ich-tanke-strom.ch API](https://github.com/SFOE/ichtankestrom_Documentation/blob/main/How%20to%20query%20ich%20tanke%20strom.md)
- [Legacy data access (OICP files, WFS)](https://github.com/SFOE/ichtankestrom_Documentation/blob/main/ich-tanke-strom-old/Access%20Download%20the%20data%20old.md)
- [opendata.swiss dataset "Ladestationen für Elektroautos"](https://opendata.swiss/dataset/ladestationen-fuer-elektroautos/)
- [OCPI 2.2.1 specification](https://evroaming.org/wp-content/uploads/2024/11/OCPI-2.2.1-d2.pdf)
