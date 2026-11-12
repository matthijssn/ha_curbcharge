# Open Charge Map (global)

Open Charge Map (OCM) is a worldwide, community-maintained directory. Coverage
and record accuracy vary; a search can cross borders, but a missing result does
not prove that no charger exists.

## Official sources

- [API documentation](https://openchargemap.org/develop/api) and
  [OpenAPI schema and fair-usage policy](https://github.com/openchargemap/ocm-docs/blob/master/Model/schema/ocm-openapi-spec.yaml).
- [Terms and attribution requirements](https://openchargemap.org/about/terms).
- Official reference data:
  [usage types](https://github.com/openchargemap/ocm-docs/blob/master/Database/Scripts/ReferenceData/dbo.UsageType.Table.sql),
  [connection types](https://github.com/openchargemap/ocm-docs/blob/master/Database/Scripts/ReferenceData/dbo.ConnectionType.Table.sql),
  and [current types](https://github.com/openchargemap/ocm-docs/blob/master/Database/Scripts/ReferenceData/dbo.CurrentType.Table.sql).

Users register an application under **My Apps** in their OCM profile and supply
their own API key. Requests send that key in the `X-API-Key` header and the
configured search coordinates to OCM. Never put keys or private coordinates
in diagnostics or logs.

An authenticated smoke test against public central London coordinates passed:
10 GB stations had unknown availability/tariffs and source/license/timestamp
metadata. Validation plus two identical discovery calls used one HTTP request;
out-of-radius favourite lookup and cached distance recalculation also passed
(four HTTP requests overall). The test used actual aiohttp and repository
models/adapter/cache, with only Home Assistant imports stubbed on Windows.
This does not replace the supported Linux Home Assistant lifecycle/coverage
checks in CI. The temporary credential file was deleted after testing.

## Retrieval and eligibility

The `/v3/poi/` endpoint is queried with a kilometre radius, JSON, full reference
objects, no comments, `opendata=true`, and usage IDs `1,4,5,7`. The reference
table marks these as public, public with membership, public with payment on
site, and public with notice respectively. Public does **not** mean free.
Private, unknown, and non-open-licensed sources are excluded again locally,
including on favourite lookup. Data providers have different licenses:
preserve the actual source title, website and license; do not assume one common
license. OCM's terms and the individual source license both apply.

Each query requests at most 500 records. OCM does not guarantee exhaustive
nearest-first ordering; a response reaching this cap is flagged as potentially
truncated even if local filtering removes records. Results are filtered by
actual distance and sorted locally. Favourites outside the search area are
looked up using bounded batches of up to 100 `chargepointid` values.

OCM's fair-use policy prohibits repeated duplicate queries. Successful validated
responses are cached in memory for one hour per key fingerprint and query,
shared by setup validation and entries. The bounded cache serializes misses to
avoid duplicate in-flight requests. Server `Retry-After` is shared per key;
failures are not cached. Responses have a 4 MiB limit, redirects are disabled,
and there is no disk cache or arbitrary global download.

## Normalization and limitations

An OCM POI ID identifies a station/location; its address contains the actual
country where supplied. A connection ID identifies a connector group, not a
fabricated EVSE. The adapter maps official IDs for J1772, CHAdeMO, Tesla,
NEMA 5-15/5-20/14-50, Type 2 and CCS 1/2; unknown types remain unknown.
Power is used only when explicitly reported. Group `Quantity` is not inferred
from `NumberOfPoints`; absent or invalid quantities make the station total
unknown.

OCM status describes a directory/operational state, not current occupancy or
opening hours. Connector availability and opening state therefore remain
unknown. `DateLastStatusUpdate` is a directory-record timestamp, not a live
occupancy observation. Free-text usage cost is not converted into a numeric
tariff. Consequently availability-dependent recommendations cannot promise a
free connector; the existing `include_unknown=false` setting can hide nearby
OCM results, while saved favourites remain additive.
