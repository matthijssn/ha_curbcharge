# Italy (PUN) provider eligibility

This note records the source gate for [#359](https://github.com/matthijssn/ha_curbcharge/issues/359).
It is a **no-go for provider implementation under the official material reviewed
on 2026-11-10**. This does not claim that PUN has no internal API; it means no
supported, reusable consumer interface was verified.

## Official source and user-facing data

The [Piattaforma Unica Nazionale (PUN)](https://pun.piattaformaunicanazionale.it/)
is the national charging-point platform promoted by Italy's Ministry of
Environment and Energy Security and built with GSE/RSE. Its public
[charging-network map/list](https://pun.piattaformaunicanazionale.it/idr)
supports location search and technical filters without requiring a login.

The official [PUN user manual](https://www.gse.it/documenti_site/Documenti%20GSE/Servizi%20per%20te/PUN/Moduli%20e%20modelli/Manuale%20Utente%20PUN.pdf)
describes point details such as point ID, address, status, delivered power,
connector type, access and activation information, opening hours, and CPO. It
also describes a real-time update label when a point's information is managed
in real time. This establishes browser-facing functionality, not a supported
machine interface or a freshness guarantee for every point.

The official [GSE PUN page](https://www.gse.it/servizi-per-te/rinnovabili-per-i-trasporti/pun)
and [document list](https://www.gse.it/servizi-per-te/rinnovabili-per-i-trasporti/pun/documenti)
describe the public portal and CPO registration/data submission. The reviewed
documents did not publish a third-party consumer API contract, access URL,
schema, or feed polling instructions.

## Reuse and access

The reviewed official PUN pages and user manual do not state a license or terms
for automated reuse, caching, transformation, or redistribution. Public access
to the map is not sufficient evidence of permission for CurbCharge to retrieve
and normalize its data.

No supported consumer authentication model or rate limits were documented.
No endpoint probe was performed because an official machine-access endpoint
and its permitted use were not identified.

## Data behavior and coverage

PUN is described as a national map/list, but this alone does not establish
complete geographic coverage or the share of records with live availability.
The manual indicates that real-time status is available only where a point's
information is managed in real time. No dynamic feed schema, per-record
timestamp contract, geographic query, pagination/delta mechanism, or
conditional-request behavior was verified.

## Eligibility decision

**No-go until an official, reusable machine interface is documented or
confirmed by PUN/GSE.** Reconsider only after confirming:

1. An officially supported machine endpoint and its reuse, attribution, and
   caching conditions.
2. Broad Italian coverage and documented status/freshness semantics.
3. Bounded nearby or incremental retrieval, with response sizes and request
   cadence appropriate for Home Assistant polling.
4. Any required user credentials can be obtained by each user and handled
   securely without maintainer-held credentials.

Do not implement by scraping PUN HTML or relying on undocumented frontend
endpoints. Do not repeatedly download full-country snapshots. If these
conditions cannot be met, keep #359 blocked and re-screen another country.
