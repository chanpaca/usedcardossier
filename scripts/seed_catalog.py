"""Build data/catalog.json: every model/year the app can recommend.

Walks the 20 covered brands across the model years that land in the
$10k-$50k used band, asks fueleconomy.gov which models existed, and records
the EPA size class and fuel economy of each. Results land in the HTTP cache
too, so a fresh clone that ships the cache never has to repeat this.

Run:  python -m scripts.seed_catalog
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict

from backend.config import CACHE_DB, DATA_DIR
from backend.data.brands import BRANDS
from backend.tools.fueleconomy import FuelEconomyClient
from backend.tools.http_cache import HttpCache

# Model years realistically available in the $10k-$50k used market in 2026.
YEARS = range(2013, 2024)

CATALOG_PATH = DATA_DIR / "catalog.json"


def main() -> int:
    cache = HttpCache(CACHE_DB)
    client = FuelEconomyClient(cache)

    entries: list[dict] = []
    by_class: dict[str, int] = defaultdict(int)
    started = time.time()

    for year in YEARS:
        for brand in BRANDS:
            try:
                models = client.models(year, brand.api_name)
            except Exception as exc:  # noqa: BLE001 - seeding is best-effort
                print(f"  ! {year} {brand.api_name}: {exc}", file=sys.stderr)
                continue
            for model in models:
                entries.append(
                    {
                        "year": year,
                        "make": brand.api_name,
                        "brand_slug": brand.slug,
                        "tier": brand.tier,
                        "model": model,
                    }
                )
        print(
            f"  {year}: {len(entries):6,} rows "
            f"({time.time() - started:5.1f}s, cache {cache.stats()})"
        )

    # Attach EPA size class by sampling one trim per model/year. This is the
    # expensive part, so it only runs for combinations we actually kept.
    print("\nresolving EPA size classes...")
    seen: dict[tuple[int, str, str], str] = {}
    for i, row in enumerate(entries):
        key = (row["year"], row["make"], row["model"])
        if key in seen:
            row["vehicle_class"] = seen[key]
            continue
        options = client.trim_options(*key)
        klass = ""
        if options:
            trim = client.vehicle(options[0]["vehicle_id"])
            klass = trim.vehicle_class if trim else ""
        seen[key] = klass
        row["vehicle_class"] = klass
        by_class[klass] += 1
        if i % 250 == 0:
            print(f"    {i:6,}/{len(entries):,}  ({time.time() - started:5.1f}s)")

    CATALOG_PATH.write_text(
        json.dumps({"years": [YEARS.start, YEARS.stop - 1], "entries": entries}),
        encoding="utf-8",
    )
    print(f"\nwrote {CATALOG_PATH} with {len(entries):,} rows")
    print(f"cache: {cache.stats()}  elapsed {time.time() - started:.0f}s")
    print("\ntop EPA classes:")
    for klass, count in sorted(by_class.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {count:5,}  {klass or '(unknown)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
