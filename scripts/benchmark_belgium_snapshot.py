"""Measure traced peak/retained allocations for synthetic Belgium discovery.

Run from the repository root with ``python scripts/benchmark_belgium_snapshot.py``.
Requires the integration's runtime dependencies, but makes no network requests.
The same script can be run on the base revision for a before/after comparison.
"""

from __future__ import annotations

import asyncio
import copy
import gc
import json
import sys
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.curbcharge.models import FavouriteStationReference  # noqa: E402
from custom_components.curbcharge.providers.belgium import (  # noqa: E402
    BelgiumChargingProvider,
    BelgiumDatexClient,
)

PAGES = 18
SITES_PER_PAGE = 1000
EVSES_PER_SITE = 4


class SyntheticResponse:
    """Generate only the requested page, without retaining its body."""

    status = 200

    def __init__(self, page: int) -> None:
        self.page = page
        self.headers = (
            {"Link": f'<?offset={(page + 1) * SITES_PER_PAGE}>; rel="next"'}
            if page + 1 < PAGES
            else {}
        )

    async def __aenter__(self) -> SyntheticResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def read(self) -> bytes:
        template = json.loads(
            Path("tests/fixtures/belgium_locations_root_example.json").read_text(
                encoding="utf-8"
            )
        )
        template["aegiEnergyInfrastructureStatusPublication"] = {
            "energyInfrastructureSiteStatus": []
        }
        table = template["aegiEnergyInfrastructureTablePublication"][
            "energyInfrastructureTable"
        ][0]
        original_site = table["energyInfrastructureSite"][0]
        table["energyInfrastructureSite"] = []
        for index in range(SITES_PER_PAGE):
            site = copy.deepcopy(original_site)
            site_id = self.page * SITES_PER_PAGE + index
            site["idG"] = f"synthetic-site-{site_id}"
            # Unused publisher metadata brings the 18-page wire size to ~130 MB.
            site["publisherMetadata"] = f"{site_id}:" + "x" * 4600
            station = site["energyInfrastructureStation"][0]
            original_point = station["refillPoint"][0]
            station["refillPoint"] = []
            for point_index in range(EVSES_PER_SITE):
                point = copy.deepcopy(original_point)
                evse = point["aegiElectricChargingPoint"]
                evse_id = site_id * EVSES_PER_SITE + point_index
                evse["idG"] = f"synthetic-evse-{evse_id}"
                evse["connector"][0]["externalIdentifier"][0]["identifier"] = (
                    f"BE*SYNTHETIC*{evse_id}"
                )
                station["refillPoint"].append(point)
            table["energyInfrastructureSite"].append(site)
        body = json.dumps(template).encode()
        self.byte_count = len(body)
        return body


class SyntheticSession:
    def __init__(self) -> None:
        self.responses: list[SyntheticResponse] = []

    def get(self, _url: str, **_kwargs: object) -> SyntheticResponse:
        response = SyntheticResponse(len(self.responses))
        self.responses.append(response)
        return response


class NoWaitLimiter:
    async def async_wait(self, *_args: object) -> None:
        pass


async def main() -> None:
    session = SyntheticSession()
    provider = BelgiumChargingProvider(
        BelgiumDatexClient(session, "synthetic-token", NoWaitLimiter()),
        static_first=True,
    )
    favourite = FavouriteStationReference(
        provider=provider.provider_id,
        provider_id=f"synthetic-evse-{PAGES * SITES_PER_PAGE * EVSES_PER_SITE - 1}",
        name="Synthetic favourite",
        latitude=50.85,
        longitude=4.35,
    )
    tracemalloc.start()
    result = await provider.async_get_stations(50.85, 4.35, 1000, 10, (favourite,))
    assert len(result.nearby) == 10 and len(result.favourites) == 1
    assert result.nearby[0].tariff is not None
    assert len(session.responses) == PAGES
    del result
    gc.collect()
    retained, peak = tracemalloc.get_traced_memory()
    print(
        json.dumps(
            {
                "pages": len(session.responses),
                "sites": PAGES * SITES_PER_PAGE,
                "evses": PAGES * SITES_PER_PAGE * EVSES_PER_SITE,
                "wire_mib": round(
                    sum(response.byte_count for response in session.responses)
                    / 1024**2,
                    2,
                ),
                "peak_traced_mib": round(peak / 1024**2, 2),
                "retained_traced_mib": round(retained / 1024**2, 2),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
