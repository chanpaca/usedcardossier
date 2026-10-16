"""Used-car value model: original MSRP -> today's market value.

No public, freely redistributable corpus of US used-car asking prices exists.
The paid feeds (Edmunds, CarMax, KBB) are closed, and the free dataset samples
we checked paywall exactly the price column. So rather than ship someone
else's scraped prices, this module models value from a figure we can always
get for free -- the original MSRP from the NHTSA vPIC VIN decoder -- using a
retention curve fitted to real listings.

    value = MSRP * exp(b0 + b_age*age + b_miles*(miles/10k) + b_body)

The fit lives in data/depreciation_params.json; `scripts/fit_depreciation.py`
reproduces it. Fit quality is deliberately published rather than buried:
R^2 = 0.60 and median absolute error ~21 percent on held-in listings. That
error is why this module returns a BAND, never a single number, and why the
UI invites the user to type the actual asking price of the car in front of
them. Our job is to judge a price, not to guess one.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import DATA_DIR

_PARAMS_PATH = DATA_DIR / "depreciation_params.json"

# fueleconomy.gov / vPIC body strings mapped onto the fitted body levels.
_BODY_ALIASES = {
    "pickup": "Pickup", "truck": "Pickup", "crew cab": "Pickup",
    "sport utility": "SUV", "suv": "SUV", "crossover": "SUV",
    "sedan": "Sedan", "saloon": "Sedan", "cars": "Sedan",
    "coupe": "Coupe",
    "hatchback": "Hatchback", "station wagon": "Hatchback", "wagon": "Hatchback",
    "convertible": "Convertible", "cabriolet": "Convertible", "roadster": "Convertible",
    "minivan": "SUV", "van": "SUV",
}


def normalise_body(raw: str) -> str:
    """Map a free-text body description onto a fitted body level."""
    text = (raw or "").strip().lower()
    for needle, level in _BODY_ALIASES.items():
        if needle in text:
            return level
    return "Sedan"   # reference level


@dataclass(frozen=True)
class ValueEstimate:
    low: int
    point: int
    high: int
    retention: float
    msrp: int
    age_years: int
    mileage: int
    body_level: str
    median_abs_pct_error: float

    @property
    def band_text(self) -> str:
        return f"${self.low:,} - ${self.high:,}"


class DepreciationModel:
    def __init__(self, params_path: Path = _PARAMS_PATH) -> None:
        with open(params_path, encoding="utf-8") as handle:
            self.params = json.load(handle)
        self.coef = self.params["coef"]
        self.low_mult = self.params["band_low_mult"]
        self.high_mult = self.params["band_high_mult"]
        self.mape = self.params["median_abs_pct_error"]

    def retention(self, age_years: int, mileage: int, body: str) -> float:
        """Fraction of original MSRP retained. Clamped to a sane range."""
        level = normalise_body(body)
        log_ret = (
            self.coef["intercept"]
            + self.coef["age"] * age_years
            + self.coef["miles_per_10k"] * (mileage / 10_000)
            + self.coef.get(f"body_{level}", 0.0)
        )
        # A car is never worth more than new, and rarely worth under 8% of MSRP
        # while still roadworthy; the fit is not trustworthy past those edges.
        return max(0.08, min(1.0, math.exp(log_ret)))

    def estimate(
        self,
        msrp: float,
        model_year: int,
        mileage: int,
        body: str,
        *,
        as_of: date | None = None,
    ) -> ValueEstimate:
        today = as_of or date.today()
        age = max(0, today.year - model_year)
        ret = self.retention(age, mileage, body)
        point = msrp * ret
        return ValueEstimate(
            low=int(round(point * self.low_mult, -2)),
            point=int(round(point, -2)),
            high=int(round(point * self.high_mult, -2)),
            retention=round(ret, 4),
            msrp=int(msrp),
            age_years=age,
            mileage=mileage,
            body_level=normalise_body(body),
            median_abs_pct_error=self.mape,
        )

    def resale_after(
        self,
        msrp: float,
        model_year: int,
        mileage_now: int,
        body: str,
        *,
        years_held: int,
        annual_miles: int,
        as_of: date | None = None,
    ) -> int:
        """Projected value after holding the car for `years_held` more years."""
        today = as_of or date.today()
        future_age = max(0, today.year - model_year) + years_held
        future_miles = mileage_now + annual_miles * years_held
        return int(round(msrp * self.retention(future_age, future_miles, body), -2))
