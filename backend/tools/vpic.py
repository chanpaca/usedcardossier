"""NHTSA vPIC client -- the original-MSRP source (free, keyless).

vPIC decodes a VIN into roughly 70 populated fields, including "Base Price ($)",
which is the manufacturer's original base MSRP for that trim. That single field
is what lets the app model used value without redistributing anyone's scraped
price data.

API docs: https://vpic.nhtsa.dot.gov/api/
"""
from __future__ import annotations

from dataclasses import dataclass

from .http_cache import HttpCache

VPIC_BASE_URL = "https://vpic.nhtsa.dot.gov/api/vehicles"


@dataclass(frozen=True)
class VinInfo:
    vin: str
    make: str
    model: str
    model_year: int | None
    trim: str
    body_class: str
    base_price: float | None     # original MSRP in USD
    cylinders: int | None
    horsepower: int | None
    engine_model: str
    drive_type: str
    doors: int | None
    errors: str


def _num(value: str | None) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


class VpicClient:
    def __init__(self, cache: HttpCache, base_url: str = VPIC_BASE_URL) -> None:
        self.cache = cache
        self.base_url = base_url

    def decode_vin(self, vin: str, model_year: int | None = None) -> VinInfo | None:
        """Decode a full or partial VIN. Partial VINs may use '*' as padding."""
        params = {"format": "json"}
        if model_year:
            params["modelyear"] = model_year
        payload = self.cache.get_json(
            self.base_url, f"/DecodeVin/{vin}", params
        )
        results = (payload or {}).get("Results") or []
        if not results:
            return None
        fields = {
            row.get("Variable", ""): (row.get("Value") or "")
            for row in results
        }

        def get(name: str) -> str:
            value = fields.get(name, "") or ""
            return "" if value.strip().lower() in {"not applicable", "null"} else value.strip()

        year = _num(get("Model Year"))
        return VinInfo(
            vin=vin,
            make=get("Make").title(),
            model=get("Model"),
            model_year=int(year) if year else None,
            trim=get("Trim") or get("Series"),
            body_class=get("Body Class"),
            base_price=_num(get("Base Price ($)")),
            cylinders=int(_num(get("Engine Number of Cylinders")) or 0) or None,
            horsepower=int(_num(get("Engine Brake (hp) From")) or 0) or None,
            engine_model=get("Engine Model"),
            drive_type=get("Drive Type"),
            doors=int(_num(get("Doors")) or 0) or None,
            errors=get("Error Text"),
        )
