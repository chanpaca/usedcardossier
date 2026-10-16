"""Build data/catalog.json from the HTTP cache alone. Makes no network calls.

`seed_catalog.py` walks the live APIs and takes several minutes. This script
reconstructs the same catalog from whatever is already cached, which makes it
useful in two situations:

  * a fresh clone that ships the seeded cache can build its catalog offline
    and in about a second, which is what keeps the 10-minute setup rule safe;
  * during development, to get a usable catalog without waiting for a full
    seed to finish.

Any model/year whose EPA size class is not yet cached is skipped rather than
fetched, so the output is a subset of the full catalog, never a wrong one.

    python -m scripts.catalog_from_cache
"""
from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
from collections import defaultdict

from backend.config import CACHE_DB, DATA_DIR
from backend.data.brands import BY_API_NAME

CATALOG_PATH = DATA_DIR / "catalog.json"

MODEL_MENU = "/vehicle/menu/model"
OPTIONS_MENU = "/vehicle/menu/options"
VEHICLE_DETAIL = re.compile(r"/vehicle/(\d+)$")


def load_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return conn.execute("SELECT url, body FROM responses").fetchall()


def main() -> int:
    conn = sqlite3.connect(CACHE_DB)
    rows = load_rows(conn)
    print(f"{len(rows):,} cached responses")

    # vehicle_id -> EPA size class
    class_by_vehicle_id: dict[str, str] = {}
    # (year, make, model) -> [vehicle_id, ...]
    options_by_key: dict[tuple[int, str, str], list[str]] = {}
    # (year, make) -> [model, ...]
    models_by_year_make: dict[tuple[int, str], list[str]] = defaultdict(list)

    for url, body in rows:
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, TypeError):
            continue
        if payload is None:
            continue

        detail = VEHICLE_DETAIL.search(urllib.parse.urlparse(url).path)
        if detail and isinstance(payload, dict) and payload.get("VClass"):
            class_by_vehicle_id[detail.group(1)] = payload["VClass"]
            continue

        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path.endswith(MODEL_MENU):
            year = params.get("year", [None])[0]
            make = params.get("make", [None])[0]
            if not year or not make:
                continue
            items = payload.get("menuItem") if isinstance(payload, dict) else payload
            if items is None:
                continue
            if isinstance(items, dict):
                items = [items]
            models_by_year_make[(int(year), make)] = [i["text"] for i in items]

        elif parsed.path.endswith(OPTIONS_MENU):
            year = params.get("year", [None])[0]
            make = params.get("make", [None])[0]
            model = params.get("model", [None])[0]
            if not (year and make and model):
                continue
            items = payload.get("menuItem") if isinstance(payload, dict) else payload
            if items is None:
                continue
            if isinstance(items, dict):
                items = [items]
            options_by_key[(int(year), make, model)] = [i["value"] for i in items]

    print(f"  {len(models_by_year_make):,} model lists")
    print(f"  {len(options_by_key):,} trim-option lookups")
    print(f"  {len(class_by_vehicle_id):,} resolved size classes")

    entries = []
    skipped = 0
    for (year, make), models in sorted(models_by_year_make.items()):
        brand = BY_API_NAME.get(make)
        if brand is None:
            continue
        for model in models:
            ids = options_by_key.get((year, make, model))
            klass = ""
            if ids:
                for vehicle_id in ids:
                    klass = class_by_vehicle_id.get(vehicle_id, "")
                    if klass:
                        break
            if not klass:
                skipped += 1
                continue
            entries.append({
                "year": year,
                "make": make,
                "brand_slug": brand.slug,
                "tier": brand.tier,
                "model": model,
                "vehicle_class": klass,
            })

    if not entries:
        print("nothing resolvable yet; let the seed run longer")
        return 1

    years = sorted({e["year"] for e in entries})
    CATALOG_PATH.write_text(
        json.dumps({"years": [years[0], years[-1]], "entries": entries}),
        encoding="utf-8",
    )
    print(f"\nwrote {CATALOG_PATH}")
    print(f"  {len(entries):,} entries, {skipped:,} skipped (class not cached yet)")
    print(f"  years {years[0]}-{years[-1]}, "
          f"{len({e['make'] for e in entries})} makes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
