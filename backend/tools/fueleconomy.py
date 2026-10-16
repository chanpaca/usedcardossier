"""Client for the fueleconomy.gov web service (free, keyless, US-only).

Supplies the trim layer of the dossier. For one year/make/model it returns the
distinct powertrain configurations the EPA tested, and for each one a full spec
line plus an annual fuel cost in dollars.

API docs: https://www.fueleconomy.gov/feg/ws/
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from ..config import FUELECONOMY_BASE_URL
from .http_cache import HttpCache


@dataclass(frozen=True)
class Trim:
    """One EPA-tested powertrain configuration of a model year."""

    vehicle_id: str
    label: str            # e.g. "Auto 10-spd, 4 cyl, 2.0 L, Turbo"
    make: str
    model: str
    year: int
    transmission: str
    cylinders: int | None
    displacement_l: float | None
    drive: str
    vehicle_class: str    # EPA size class, used for segment comparisons
    fuel_type: str
    mpg_city: int | None
    mpg_highway: int | None
    mpg_combined: int | None
    annual_fuel_cost: int | None   # USD/yr at EPA's assumed 15k mi and fuel price
    co2_g_per_mile: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_int(value: Any) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number or None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _menu_items(payload: Any) -> list[dict[str, str]]:
    """Normalise the menu endpoints, which return a dict for single results."""
    if not payload:
        return []
    items = payload.get("menuItem") if isinstance(payload, dict) else payload
    if items is None:
        return []
    return [items] if isinstance(items, dict) else list(items)


class FuelEconomyClient:
    def __init__(self, cache: HttpCache, base_url: str = FUELECONOMY_BASE_URL) -> None:
        self.cache = cache
        self.base_url = base_url

    def makes(self, year: int) -> list[str]:
        payload = self.cache.get_json(
            self.base_url, "/vehicle/menu/make", {"year": year}
        )
        return [item["text"] for item in _menu_items(payload)]

    def models(self, year: int, make: str) -> list[str]:
        payload = self.cache.get_json(
            self.base_url, "/vehicle/menu/model", {"year": year, "make": make}
        )
        return [item["text"] for item in _menu_items(payload)]

    def trim_options(self, year: int, make: str, model: str) -> list[dict[str, str]]:
        """The distinct powertrain configs for a model year: label + vehicle id."""
        payload = self.cache.get_json(
            self.base_url,
            "/vehicle/menu/options",
            {"year": year, "make": make, "model": model},
        )
        return [
            {"vehicle_id": item["value"], "label": item["text"]}
            for item in _menu_items(payload)
        ]

    def vehicle(self, vehicle_id: str) -> Trim | None:
        """Full spec + annual fuel cost for one trim."""
        payload = self.cache.get_json(self.base_url, f"/vehicle/{vehicle_id}")
        if not payload or not payload.get("make"):
            return None
        return Trim(
            vehicle_id=str(vehicle_id),
            label=payload.get("trany", ""),
            make=payload.get("make", ""),
            model=payload.get("model", ""),
            year=int(payload.get("year", 0) or 0),
            transmission=payload.get("trany", ""),
            cylinders=_as_int(payload.get("cylinders")),
            displacement_l=_as_float(payload.get("displ")),
            drive=payload.get("drive", ""),
            vehicle_class=payload.get("VClass", ""),
            fuel_type=payload.get("fuelType", ""),
            mpg_city=_as_int(payload.get("city08")),
            mpg_highway=_as_int(payload.get("highway08")),
            mpg_combined=_as_int(payload.get("comb08")),
            annual_fuel_cost=_as_int(payload.get("fuelCost08")),
            co2_g_per_mile=_as_float(payload.get("co2TailpipeGpm")),
        )

    def trims(self, year: int, make: str, model: str) -> list[Trim]:
        """Every trim of a model year, with specs resolved. One call per trim."""
        resolved = []
        for option in self.trim_options(year, make, model):
            trim = self.vehicle(option["vehicle_id"])
            if trim is not None:
                # The menu label is richer than the bare transmission string.
                resolved.append(
                    Trim(**{**trim.to_dict(), "label": option["label"]})
                )
        return resolved
