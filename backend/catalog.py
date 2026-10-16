"""Query layer over data/catalog.json -- which cars the app can recommend.

The catalog is the hard filter that runs BEFORE any agent sees anything. Body
style, brand, budget and model year are matched here in Python, so the LLM
only ever ranks a short list of vehicles that already qualify. That keeps
tokens down and makes it impossible for an agent to recommend a car that
falls outside the buyer's constraints.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from .config import DATA_DIR
from .data.brands import BY_API_NAME

CATALOG_PATH = DATA_DIR / "catalog.json"

# EPA size classes grouped into the body styles a buyer actually picks from.
BODY_STYLES: dict[str, tuple[str, ...]] = {
    "sedan": (
        "Compact Cars", "Midsize Cars", "Large Cars", "Subcompact Cars",
        "Minicompact Cars",
    ),
    "suv": (
        "Small Sport Utility Vehicle", "Standard Sport Utility Vehicle",
        "Sport Utility Vehicle", "Small Sport Utility Vehicle 2WD",
        "Small Sport Utility Vehicle 4WD",
        "Standard Sport Utility Vehicle 2WD",
        "Standard Sport Utility Vehicle 4WD",
    ),
    "truck": (
        "Standard Pickup Trucks", "Small Pickup Trucks",
        "Standard Pickup Trucks 2WD", "Standard Pickup Trucks 4WD",
        "Small Pickup Trucks 2WD", "Small Pickup Trucks 4WD",
    ),
    "wagon": (
        "Small Station Wagons", "Midsize Station Wagons",
        "Midsize-Large Station Wagons",
    ),
    "van": ("Minivan - 2WD", "Minivan - 4WD", "Vans, Passenger Type", "Vans"),
}

BODY_STYLE_LABELS = {
    "sedan": "Sedan or hatchback",
    "suv": "SUV or crossover",
    "truck": "Pickup truck",
    "wagon": "Station wagon",
    "van": "Minivan or van",
}

# Reverse index: EPA class -> body style key.
_CLASS_TO_STYLE = {
    klass: style for style, classes in BODY_STYLES.items() for klass in classes
}


def classify_body(vehicle_class: str) -> str | None:
    """Map an EPA size class onto a body style, if we cover it."""
    if not vehicle_class:
        return None
    if vehicle_class in _CLASS_TO_STYLE:
        return _CLASS_TO_STYLE[vehicle_class]
    text = vehicle_class.lower()
    if "pickup" in text:
        return "truck"
    if "sport utility" in text:
        return "suv"
    if "station wagon" in text:
        return "wagon"
    if "van" in text:
        return "van"
    if "cars" in text:
        return "sedan"
    return None


@dataclass(frozen=True)
class CatalogEntry:
    year: int
    make: str
    model: str
    brand_slug: str
    tier: str
    vehicle_class: str
    body_style: str

    @property
    def id(self) -> str:
        return f"{self.year}|{self.make}|{self.model}"

    @property
    def label(self) -> str:
        return f"{self.year} {self.make} {self.model}"


@lru_cache(maxsize=1)
def load_catalog() -> tuple[CatalogEntry, ...]:
    if not CATALOG_PATH.is_file():
        raise FileNotFoundError(
            f"{CATALOG_PATH} missing. Run: python -m scripts.seed_catalog"
        )
    with open(CATALOG_PATH, encoding="utf-8") as handle:
        raw = json.load(handle)

    entries = []
    for row in raw["entries"]:
        style = classify_body(row.get("vehicle_class", ""))
        if style is None:
            continue
        entries.append(
            CatalogEntry(
                year=row["year"],
                make=row["make"],
                model=row["model"],
                brand_slug=row["brand_slug"],
                tier=row["tier"],
                vehicle_class=row["vehicle_class"],
                body_style=style,
            )
        )
    return tuple(entries)


def search(
    *,
    body_style: str | None = None,
    brand_slugs: list[str] | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    limit: int | None = None,
) -> list[CatalogEntry]:
    """Hard filter. Runs before any agent sees a candidate."""
    results = []
    wanted_brands = set(brand_slugs or [])
    for entry in load_catalog():
        if body_style and entry.body_style != body_style:
            continue
        if wanted_brands and entry.brand_slug not in wanted_brands:
            continue
        if year_min and entry.year < year_min:
            continue
        if year_max and entry.year > year_max:
            continue
        results.append(entry)
    results.sort(key=lambda e: (-e.year, e.make, e.model))
    return results[:limit] if limit else results


def available_brands(body_style: str | None = None) -> list[dict[str, str]]:
    """Brands that actually have models in this body style."""
    slugs = {
        e.brand_slug
        for e in load_catalog()
        if not body_style or e.body_style == body_style
    }
    out = []
    for brand in BY_API_NAME.values():
        if brand.slug in slugs:
            out.append(
                {"slug": brand.slug, "name": brand.display_name, "tier": brand.tier}
            )
    return sorted(out, key=lambda b: b["name"])


def stats() -> dict[str, int]:
    entries = load_catalog()
    by_style: dict[str, int] = {}
    for entry in entries:
        by_style[entry.body_style] = by_style.get(entry.body_style, 0) + 1
    return {"total": len(entries), **by_style}
