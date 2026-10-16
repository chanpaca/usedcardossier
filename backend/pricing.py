"""Resolve an original MSRP for a vehicle, by the most precise route available.

Three routes, best first:

  1. VIN         -- NHTSA vPIC returns the exact "Base Price ($)" for that
                    trim. Precise. Used whenever the buyer has a real car.
  2. make+model  -- median original MSRP from an aggregate reference table.
  3. body style  -- median for the body type. Last resort, very approximate.

Only routes 2 and 3 are estimates, and both report `confidence` so the UI can
say so instead of implying a precision it does not have.

On the reference table: it holds MEDIANS ONLY, computed from a public dataset
sample. No listing-level record is stored or redistributed. See
data/msrp_reference.json for the provenance string.

Known bias, stated because it affects output: the underlying sample skews
toward newer, more expensive and electric vehicles, so the body-level medians
run high for ordinary mainstream cars. This is why route 3 is marked "low"
confidence and why the UI pushes the buyer toward entering a VIN or an actual
asking price.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .config import DATA_DIR

MSRP_REFERENCE_PATH = DATA_DIR / "msrp_reference.json"


@dataclass(frozen=True)
class MsrpEstimate:
    value: float
    source: str          # "vin" | "make_model" | "body" | "global"
    confidence: str      # "exact" | "medium" | "low"
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": int(self.value),
            "source": self.source,
            "confidence": self.confidence,
            "note": self.note,
        }


@lru_cache(maxsize=1)
def _reference() -> dict[str, Any]:
    if not MSRP_REFERENCE_PATH.is_file():
        return {"by_make_model": {}, "by_body": {}, "global_median": 30000}
    with open(MSRP_REFERENCE_PATH, encoding="utf-8") as handle:
        return json.load(handle)


# fueleconomy/EPA body style keys -> reference table body keys.
_BODY_MAP = {
    "sedan": "Sedan",
    "suv": "SUV",
    "truck": "Pickup",
    "wagon": "Wagon",
    "van": "Minivan",
}


def msrp_from_vin(base_price: float | None) -> MsrpEstimate | None:
    if not base_price or base_price <= 0:
        return None
    return MsrpEstimate(
        value=base_price,
        source="vin",
        confidence="exact",
        note="Original base MSRP decoded from the VIN by NHTSA vPIC.",
    )


def estimate_msrp(make: str, model: str, body_style: str) -> MsrpEstimate:
    """Best available MSRP when there is no VIN."""
    ref = _reference()

    key = f"{make}|{model}"
    direct = ref.get("by_make_model", {}).get(key)
    if direct:
        return MsrpEstimate(
            value=float(direct),
            source="make_model",
            confidence="medium",
            note=(
                f"Median original MSRP for {make} {model} from the reference "
                f"table. Not trim-specific -- enter a VIN for the exact figure."
            ),
        )

    body_key = _BODY_MAP.get(body_style, "")
    body_median = ref.get("by_body", {}).get(body_key)
    if body_median:
        return MsrpEstimate(
            value=float(body_median),
            source="body",
            confidence="low",
            note=(
                f"No model-level figure available, so this uses the median for "
                f"{body_key} vehicles. The reference sample skews new and "
                f"premium, so this likely overstates MSRP for a mainstream "
                f"model. Treat the resulting price band as indicative only."
            ),
        )

    return MsrpEstimate(
        value=float(ref.get("global_median", 30000)),
        source="global",
        confidence="low",
        note="No model or body figure available; using the overall median.",
    )
