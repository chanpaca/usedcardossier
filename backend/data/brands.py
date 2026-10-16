"""The 20 brands the app covers.

Scope is deliberately limited to the highest-volume mainstream brands in the US
used market. Exotics, defunct marques, and low-volume EV startups are excluded:
their parts and service economics make total-cost-of-ownership a different
analysis, and thin listing volume makes any price band unreliable.

`api_name` is the exact string fueleconomy.gov and the NHTSA APIs expect; it is
verified against the live make list, not guessed.

NOTE: `maintenance_index` is intentionally absent. Per-brand repair cost is a
sourced figure, not something to invent -- see backend/data/maintenance.py,
which loads it from a cited dataset. Repair *risk* is computed separately from
NHTSA complaint density.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Brand:
    slug: str
    display_name: str
    api_name: str      # exact token for fueleconomy.gov / NHTSA
    tier: str          # "mainstream" | "luxury"
    origin: str        # manufacturer home market, affects parts sourcing


BRANDS: tuple[Brand, ...] = (
    Brand("toyota",        "Toyota",        "Toyota",        "mainstream", "Japan"),
    Brand("ford",          "Ford",          "Ford",          "mainstream", "USA"),
    Brand("chevrolet",     "Chevrolet",     "Chevrolet",     "mainstream", "USA"),
    Brand("honda",         "Honda",         "Honda",         "mainstream", "Japan"),
    Brand("nissan",        "Nissan",        "Nissan",        "mainstream", "Japan"),
    Brand("jeep",          "Jeep",          "Jeep",          "mainstream", "USA"),
    Brand("hyundai",       "Hyundai",       "Hyundai",       "mainstream", "South Korea"),
    Brand("kia",           "Kia",           "Kia",           "mainstream", "South Korea"),
    Brand("gmc",           "GMC",           "GMC",           "mainstream", "USA"),
    Brand("subaru",        "Subaru",        "Subaru",        "mainstream", "Japan"),
    Brand("ram",           "Ram",           "Ram",           "mainstream", "USA"),
    Brand("dodge",         "Dodge",         "Dodge",         "mainstream", "USA"),
    Brand("mazda",         "Mazda",         "Mazda",         "mainstream", "Japan"),
    Brand("volkswagen",    "Volkswagen",    "Volkswagen",    "mainstream", "Germany"),
    Brand("buick",         "Buick",         "Buick",         "mainstream", "USA"),
    Brand("bmw",           "BMW",           "BMW",           "luxury",     "Germany"),
    Brand("mercedes-benz", "Mercedes-Benz", "Mercedes-Benz", "luxury",     "Germany"),
    Brand("audi",          "Audi",          "Audi",          "luxury",     "Germany"),
    Brand("lexus",         "Lexus",         "Lexus",         "luxury",     "Japan"),
    Brand("cadillac",      "Cadillac",      "Cadillac",      "luxury",     "USA"),
)

BY_SLUG = {b.slug: b for b in BRANDS}
BY_API_NAME = {b.api_name: b for b in BRANDS}
API_NAMES = frozenset(b.api_name for b in BRANDS)


def get(slug: str) -> Brand:
    try:
        return BY_SLUG[slug]
    except KeyError:
        raise KeyError(f"unknown brand {slug!r}; covered: {sorted(BY_SLUG)}") from None


def is_covered(api_name: str) -> bool:
    return api_name in API_NAMES
