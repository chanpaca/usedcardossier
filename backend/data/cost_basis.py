"""Every dollar constant the TCO engine uses, with its source attached.

Rule for this file: no number appears here without a citation. Anything the
model cannot source is either computed from live data (NHTSA, fueleconomy.gov,
vPIC) or declared as a modelling assumption in ASSUMPTIONS below, where the
report and the UI can both display it.

Primary source
--------------
AAA, "Your Driving Costs 2025" fact sheet (published Sept 2025).
https://newsroom.aaa.com/wp-content/uploads/2025/09/UPDATE-AAA-Fact-Sheet-Your-Driving-Cost-9.2025-1.pdf
Basis: five top-selling models in each of nine categories, averaged over five
years and 75,000 miles. Data sourced by AAA from Vincentric LLC.

IMPORTANT CAVEAT, stated plainly because it affects every output: AAA measures
NEW vehicles. This app analyses USED ones. Three corrections follow from that
and each is applied explicitly rather than silently:

  * Depreciation -- NOT taken from AAA. Modelled from original MSRP with a
    curve fitted to real used listings (see depreciation.py). A used car loses
    far less absolute value per year than a new one.
  * Maintenance  -- AAA's figure bundles a comprehensive extended warranty, so
    it understates an out-of-warranty used car. Corrected by OUT_OF_WARRANTY_
    MULTIPLIER plus a per-vehicle NHTSA complaint-density adjustment.
  * Insurance    -- AAA's figure is for a new car. Scaled down by vehicle value.
"""
from __future__ import annotations

from dataclasses import dataclass

SOURCE_AAA_2025 = (
    'AAA "Your Driving Costs 2025" fact sheet, Sept 2025 '
    "(basis: 5 years / 75,000 miles; data from Vincentric LLC)"
)
SOURCE_AAA_URL = (
    "https://newsroom.aaa.com/wp-content/uploads/2025/09/"
    "UPDATE-AAA-Fact-Sheet-Your-Driving-Cost-9.2025-1.pdf"
)


@dataclass(frozen=True)
class SourcedValue:
    """A constant that can show its work in the UI and the report."""

    value: float
    unit: str
    source: str
    url: str
    note: str = ""


# --- AAA 2025 national averages, new vehicle -------------------------------

MAINTENANCE_PER_MILE = SourcedValue(
    0.1104, "USD/mile", SOURCE_AAA_2025, SOURCE_AAA_URL,
    "Routine maintenance, wear-and-tear repair, one set of tires, and a "
    "comprehensive extended warranty. The warranty is why this understates an "
    "out-of-warranty used car.",
)

INSURANCE_PER_YEAR_NEW = SourcedValue(
    1694.0, "USD/year", SOURCE_AAA_2025, SOURCE_AAA_URL,
    "Full-coverage policy, driver under 65 with 6+ years experience, no "
    "accidents, suburban or city. Varies widely by state and driver.",
)

REGISTRATION_TAX_PER_YEAR = SourcedValue(
    813.0, "USD/year", SOURCE_AAA_2025, SOURCE_AAA_URL,
    "Government taxes and fees at purchase plus annual registration, "
    "national average.",
)

GAS_PRICE_PER_GALLON = SourcedValue(
    3.151, "USD/gallon", SOURCE_AAA_2025, SOURCE_AAA_URL,
    "Regular grade, 12-month average ending 5/2025. Used only to restate "
    "EPA fuel costs at a different gas price.",
)

# EPA publishes fuelCost08 at an assumed 15,000 mi/yr. Needed to rescale that
# figure to a user's actual mileage.
EPA_ASSUMED_ANNUAL_MILES = SourcedValue(
    15000, "miles/year", "EPA / fueleconomy.gov annual fuel cost basis",
    "https://www.fueleconomy.gov/feg/ws/",
)

# AAA total cost of ownership by category, cents/mile. Not used as a direct
# input -- the engine computes each line item separately -- but retained so the
# report can sanity-check our totals against an independent published figure.
AAA_TOTAL_CENTS_PER_MILE: dict[str, float] = {
    "Small Sedan": 55.87,
    "Medium Sedan": 66.37,
    "Subcompact SUV": 66.11,
    "Compact SUV": 68.53,
    "Medium SUV": 83.89,
    "Mid-size Pickup": 79.11,
    "Half-Ton Pickup": 98.54,
    "Hybrid": 63.94,
    "Electric": 71.21,
}


# --- declared modelling assumptions ----------------------------------------
# These are NOT sourced measurements. They are our assumptions, named here so
# the UI can show them and a reader can disagree with a specific number.

@dataclass(frozen=True)
class Assumption:
    key: str
    value: float
    rationale: str


ASSUMPTIONS: tuple[Assumption, ...] = (
    Assumption(
        "out_of_warranty_multiplier", 1.35,
        "AAA's maintenance figure includes a comprehensive extended warranty. "
        "Most used cars in the $10k-$50k band are outside the factory bumper-"
        "to-bumper period, so the owner pays for failures the AAA basis covers. "
        "Applied to the AAA per-mile rate. This is an assumption, not a "
        "measurement -- it is the single largest judgement call in the model.",
    ),
    Assumption(
        "maintenance_age_slope", 0.025,
        "Additional fraction of the base maintenance rate added per year of "
        "vehicle age at purchase, reflecting that older cars need more work. "
        "Capped at +50 percent.",
    ),
    Assumption(
        "insurance_value_floor", 0.55,
        "Insurance does not fall proportionally with vehicle value -- liability "
        "and medical coverage are value-independent. Premium is scaled by "
        "vehicle value but never below this fraction of the AAA new-car figure.",
    ),
    Assumption(
        "complaint_risk_max_uplift", 0.40,
        "Maximum additional maintenance applied to a vehicle whose NHTSA "
        "complaint density is far above its segment median. Bounded so a "
        "noisy signal cannot dominate the estimate.",
    ),
)

ASSUMPTION_BY_KEY = {a.key: a for a in ASSUMPTIONS}


def assume(key: str) -> float:
    return ASSUMPTION_BY_KEY[key].value
