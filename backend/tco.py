"""Five-year total cost of ownership. Deterministic, no LLM involved.

This is the heart of the product and it is ordinary arithmetic on purpose.
Language models are unreliable at multi-step arithmetic and cannot be audited
after the fact; every dollar below is computed in Python from a sourced
constant or a live API value. The agents explain this output. They never
produce it.

Cost lines
----------
depreciation   value today minus projected value after the hold period
fuel           EPA per-trim annual cost, rescaled to the user's real mileage
maintenance    AAA per-mile rate, corrected for warranty status, vehicle age
               and that vehicle's NHTSA complaint density versus its segment
insurance      AAA new-car premium scaled by vehicle value, with a floor
registration   AAA national average
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .data.cost_basis import (
    GAS_PRICE_PER_GALLON,
    EPA_ASSUMED_ANNUAL_MILES,
    INSURANCE_PER_YEAR_NEW,
    MAINTENANCE_PER_MILE,
    REGISTRATION_TAX_PER_YEAR,
    assume,
)

DEFAULT_HOLD_YEARS = 5
DEFAULT_ANNUAL_MILES = 12_000


@dataclass(frozen=True)
class CostLine:
    """One cost category, carrying its own explanation."""

    key: str
    label: str
    total: int            # USD over the whole hold period
    per_year: int
    basis: str            # how it was computed, shown in the UI
    is_modelled: bool     # True when it rests on a declared assumption

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TcoResult:
    purchase_price: int
    hold_years: int
    annual_miles: int
    lines: tuple[CostLine, ...]
    projected_resale: int
    complaint_risk_ratio: float   # 1.0 == segment median
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return sum(line.total for line in self.lines)

    @property
    def per_year(self) -> int:
        return round(self.total / self.hold_years)

    @property
    def per_mile(self) -> float:
        miles = self.annual_miles * self.hold_years
        return round(self.total / miles, 3) if miles else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "purchase_price": self.purchase_price,
            "hold_years": self.hold_years,
            "annual_miles": self.annual_miles,
            "projected_resale": self.projected_resale,
            "complaint_risk_ratio": self.complaint_risk_ratio,
            "total": self.total,
            "per_year": self.per_year,
            "per_mile": self.per_mile,
            "lines": [line.to_dict() for line in self.lines],
            "warnings": list(self.warnings),
        }


def maintenance_rate_per_mile(
    vehicle_age_at_purchase: int, complaint_risk_ratio: float
) -> tuple[float, str]:
    """AAA base rate corrected for warranty status, age, and defect history."""
    base = MAINTENANCE_PER_MILE.value
    warranty = assume("out_of_warranty_multiplier")

    age_uplift = min(0.50, assume("maintenance_age_slope") * vehicle_age_at_purchase)

    # Complaint density relative to the segment median. A ratio of 2.0 means
    # twice the complaints of a typical car in the same class and era. Mapped
    # onto a bounded uplift so a noisy NHTSA signal cannot run away with the
    # estimate.
    cap = assume("complaint_risk_max_uplift")
    risk_uplift = max(-cap / 2, min(cap, (complaint_risk_ratio - 1.0) * 0.25))

    rate = base * warranty * (1 + age_uplift) * (1 + risk_uplift)
    basis = (
        f"AAA {base * 100:.2f} c/mi x {warranty:.2f} out-of-warranty "
        f"x {1 + age_uplift:.2f} age ({vehicle_age_at_purchase} yr) "
        f"x {1 + risk_uplift:.2f} NHTSA complaint density "
        f"({complaint_risk_ratio:.2f}x segment median) "
        f"= {rate * 100:.2f} c/mi"
    )
    return rate, basis


def insurance_per_year(vehicle_value: float, new_car_msrp: float) -> tuple[float, str]:
    """AAA new-car premium scaled by value, floored for value-independent cover."""
    base = INSURANCE_PER_YEAR_NEW.value
    floor = assume("insurance_value_floor")
    ratio = max(floor, min(1.0, vehicle_value / new_car_msrp)) if new_car_msrp else 1.0
    premium = base * ratio
    basis = (
        f"AAA ${base:,.0f}/yr new-car average x {ratio:.2f} value ratio "
        f"(floored at {floor:.2f} for liability and medical cover) "
        f"= ${premium:,.0f}/yr"
    )
    return premium, basis


def fuel_per_year(
    epa_annual_fuel_cost: int | None, annual_miles: int
) -> tuple[float, str]:
    """Rescale the EPA per-trim annual fuel cost to the user's real mileage."""
    if not epa_annual_fuel_cost:
        return 0.0, "No EPA fuel cost published for this trim."
    epa_miles = EPA_ASSUMED_ANNUAL_MILES.value
    scaled = epa_annual_fuel_cost * (annual_miles / epa_miles)
    basis = (
        f"EPA ${epa_annual_fuel_cost:,}/yr for this exact trim at "
        f"{epa_miles:,.0f} mi/yr (gas at ${GAS_PRICE_PER_GALLON.value:.2f}/gal), "
        f"rescaled to {annual_miles:,} mi/yr = ${scaled:,.0f}/yr"
    )
    return scaled, basis


def compute_tco(
    *,
    purchase_price: int,
    projected_resale: int,
    msrp: float,
    vehicle_age_at_purchase: int,
    epa_annual_fuel_cost: int | None,
    complaint_risk_ratio: float = 1.0,
    hold_years: int = DEFAULT_HOLD_YEARS,
    annual_miles: int = DEFAULT_ANNUAL_MILES,
) -> TcoResult:
    """Assemble the five-year cost picture."""
    warnings: list[str] = []
    total_miles = annual_miles * hold_years

    depreciation = max(0, purchase_price - projected_resale)

    fuel_yr, fuel_basis = fuel_per_year(epa_annual_fuel_cost, annual_miles)
    if not epa_annual_fuel_cost:
        warnings.append(
            "EPA publishes no fuel cost for this trim, so fuel is excluded "
            "from the total. The real total is higher."
        )

    maint_rate, maint_basis = maintenance_rate_per_mile(
        vehicle_age_at_purchase, complaint_risk_ratio
    )
    insurance_yr, ins_basis = insurance_per_year(purchase_price, msrp)

    lines = (
        CostLine(
            "depreciation", "Depreciation", depreciation,
            round(depreciation / hold_years),
            f"${purchase_price:,} paid today minus ${projected_resale:,} "
            f"projected value after {hold_years} yr and "
            f"{total_miles:,} more miles",
            is_modelled=True,
        ),
        CostLine(
            "fuel", "Fuel", round(fuel_yr * hold_years), round(fuel_yr),
            fuel_basis, is_modelled=False,
        ),
        CostLine(
            "maintenance", "Maintenance, repair and tires",
            round(maint_rate * total_miles), round(maint_rate * annual_miles),
            maint_basis, is_modelled=True,
        ),
        CostLine(
            "insurance", "Insurance", round(insurance_yr * hold_years),
            round(insurance_yr), ins_basis, is_modelled=True,
        ),
        CostLine(
            "registration", "Registration, taxes and fees",
            round(REGISTRATION_TAX_PER_YEAR.value * hold_years),
            round(REGISTRATION_TAX_PER_YEAR.value),
            f"AAA national average ${REGISTRATION_TAX_PER_YEAR.value:,.0f}/yr",
            is_modelled=False,
        ),
    )

    return TcoResult(
        purchase_price=purchase_price,
        hold_years=hold_years,
        annual_miles=annual_miles,
        lines=lines,
        projected_resale=projected_resale,
        complaint_risk_ratio=round(complaint_risk_ratio, 3),
        warnings=tuple(warnings),
    )
