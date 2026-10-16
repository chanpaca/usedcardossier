"""The five agents and the two workflows that run them.

    Discovery  : Profiler -> (hard filter + deterministic scoring) -> Scout
    Dossier    : Dossier -> Analyst -> Critic

Division of labour, which is the core design decision of this app:

    Python decides and calculates.   Agents explain and check.

Every dollar is computed in backend/tco.py. Every defect comes from an NHTSA
record. The agents receive those results as input and turn them into prose.
The Critic then re-checks that prose against the same source data, so a
fabricated claim has to survive a check by a model that can see the ground
truth. Nothing an agent says can change a number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..catalog import CatalogEntry
from ..llm import LLMClient, UsageLedger
from ..risk import RiskSignal
from ..tco import TcoResult
from . import prompts

# Agents see a trimmed view of each candidate. Sending the full object would
# multiply prompt tokens for data the model has no use for.
MAX_CANDIDATES_TO_AGENT = 12
MAX_RECALLS_TO_AGENT = 8


@dataclass
class BuyerProfile:
    body_style: str
    brand_slugs: list[str]
    priorities: list[str]
    budget_min: int
    budget_max: int
    annual_miles: int = 12_000
    hold_years: int = 5
    year_min: int | None = None
    year_max: int | None = None

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "body_style": self.body_style,
            "preferred_brands": self.brand_slugs or ["no preference"],
            "priorities": self.priorities,
            "budget_usd": [self.budget_min, self.budget_max],
            "annual_miles": self.annual_miles,
            "years_planning_to_keep": self.hold_years,
        }


@dataclass
class ScoringSpec:
    """What the Profiler produced. Weights drive deterministic ranking."""

    weights: dict[str, float]
    tensions: list[str] = field(default_factory=list)
    notes: str = ""

    DEFAULT_WEIGHTS = {
        "reliability": 0.25,
        "running_cost": 0.25,
        "safety": 0.20,
        "comfort": 0.15,
        "value_retention": 0.15,
    }

    @classmethod
    def fallback(cls, reason: str) -> ScoringSpec:
        return cls(
            weights=dict(cls.DEFAULT_WEIGHTS),
            tensions=[],
            notes=f"Using balanced default weights ({reason}).",
        )

    def normalised(self) -> dict[str, float]:
        total = sum(self.weights.values())
        if total <= 0:
            return dict(self.DEFAULT_WEIGHTS)
        return {k: v / total for k, v in self.weights.items()}


@dataclass
class Candidate:
    """A vehicle that passed the hard filter, with its computed numbers."""

    entry: CatalogEntry
    value_point: int
    value_low: int
    value_high: int
    tco: TcoResult
    risk: RiskSignal
    annual_fuel_cost: int | None
    ncap_overall: str | None
    # How the original MSRP was resolved: "exact" (VIN), "medium" (model-level
    # median) or "low" (body-level median). A low-confidence MSRP makes the
    # whole price band rough, and the UI has to say so rather than show a
    # precise-looking range it cannot support.
    msrp_confidence: str = "low"
    score: float = 0.0

    @property
    def id(self) -> str:
        return self.entry.id

    def to_agent_dict(self) -> dict[str, Any]:
        """The trimmed view an agent sees. Keep this small on purpose."""
        concerns = [
            f"{f.system} {f.ratio:.1f}x segment" for f in self.risk.concerns[:2]
        ]
        return {
            "id": self.id,
            "car": self.entry.label,
            "est_price_band": f"${self.value_low:,}-${self.value_high:,}",
            "five_year_cost": f"${self.tco.total:,}",
            "cost_per_year": f"${self.tco.per_year:,}",
            "annual_fuel": f"${self.annual_fuel_cost:,}" if self.annual_fuel_cost else "n/a",
            "ncap_stars": self.ncap_overall or "not rated",
            "complaint_flags": concerns or ["none above segment norm"],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "year": self.entry.year,
            "make": self.entry.make,
            "model": self.entry.model,
            "body_style": self.entry.body_style,
            "vehicle_class": self.entry.vehicle_class,
            "value_low": self.value_low,
            "value_point": self.value_point,
            "value_high": self.value_high,
            "msrp_confidence": self.msrp_confidence,
            "annual_fuel_cost": self.annual_fuel_cost,
            "ncap_overall": self.ncap_overall,
            "score": round(self.score, 4),
            "tco": self.tco.to_dict(),
            "risk": {
                "total_complaints": self.risk.total_complaints,
                "confidence": self.risk.confidence,
                "explanation": self.risk.explanation,
                "cost_ratio": self.risk.cost_ratio,
                "flags": [
                    {
                        "system": f.system,
                        "complaints": f.complaints,
                        "share": round(f.share, 4),
                        "segment_share": round(f.segment_share, 4),
                        "ratio": f.ratio,
                        "severity": f.severity,
                        "summary": f.summary,
                    }
                    for f in self.risk.flags
                ],
            },
        }


# --- agent 1: Profiler ------------------------------------------------------


def run_profiler(
    llm: LLMClient, profile: BuyerProfile, ledger: UsageLedger
) -> ScoringSpec:
    import json

    result = llm.complete_json(
        agent="profiler",
        system=prompts.PROFILER,
        user=json.dumps(profile.to_prompt_dict()),
        ledger=ledger,
        max_tokens=2000,
    )
    weights = result.get("weights")
    if not isinstance(weights, dict) or not weights:
        return ScoringSpec.fallback("profiler returned no usable weights")

    clean: dict[str, float] = {}
    for key in ScoringSpec.DEFAULT_WEIGHTS:
        try:
            clean[key] = max(0.0, float(weights.get(key, 0.0)))
        except (TypeError, ValueError):
            clean[key] = 0.0
    if sum(clean.values()) <= 0:
        return ScoringSpec.fallback("profiler weights were all zero")

    tensions = [str(t) for t in (result.get("tensions") or []) if str(t).strip()]
    return ScoringSpec(
        weights=clean, tensions=tensions[:4], notes=str(result.get("notes", ""))[:300]
    )


# --- deterministic ranking (no LLM) ----------------------------------------


def score_candidates(candidates: list[Candidate], spec: ScoringSpec) -> None:
    """Rank candidates in Python. The agent never reorders this."""
    if not candidates:
        return
    weights = spec.normalised()

    def spread(values: list[float]) -> tuple[float, float]:
        lo, hi = min(values), max(values)
        return lo, (hi - lo) or 1.0

    cost_lo, cost_rng = spread([c.tco.total for c in candidates])
    fuel_lo, fuel_rng = spread(
        [float(c.annual_fuel_cost or 0) for c in candidates]
    )
    dep_lo, dep_rng = spread(
        [
            c.tco.lines[0].total / max(1, c.value_point)
            for c in candidates
        ]
    )

    for cand in candidates:
        # Each component is 0..1 where 1 is better.
        running_cost = 1 - ((cand.tco.total - cost_lo) / cost_rng)
        fuel = 1 - ((float(cand.annual_fuel_cost or 0) - fuel_lo) / fuel_rng)
        # Reliability: segment-relative complaint signal, 1.0 == typical.
        reliability = max(0.0, min(1.0, 1.5 - cand.risk.cost_ratio))
        try:
            safety = float(cand.ncap_overall or 0) / 5.0
        except (TypeError, ValueError):
            safety = 0.6   # unrated: neutral, neither rewarded nor punished
        value_retention = 1 - (
            (cand.tco.lines[0].total / max(1, cand.value_point) - dep_lo) / dep_rng
        )
        # Comfort has no free public data source. Using vehicle class as a
        # proxy would be inventing a measurement, so it is held neutral and
        # the UI says so rather than pretending otherwise.
        comfort = 0.5

        cand.score = (
            weights["reliability"] * reliability
            + weights["running_cost"] * (0.6 * running_cost + 0.4 * fuel)
            + weights["safety"] * safety
            + weights["comfort"] * comfort
            + weights["value_retention"] * value_retention
        )

    candidates.sort(key=lambda c: -c.score)


# --- agent 2: Scout ---------------------------------------------------------


def run_scout(
    llm: LLMClient,
    candidates: list[Candidate],
    profile: BuyerProfile,
    spec: ScoringSpec,
    ledger: UsageLedger,
) -> dict[str, str]:
    """Returns {candidate_id: rationale}. Ranking already happened in Python."""
    import json

    if not candidates:
        return {}
    shortlist = candidates[:MAX_CANDIDATES_TO_AGENT]
    payload = {
        "buyer_priorities": profile.priorities,
        "weights": {k: round(v, 2) for k, v in spec.normalised().items()},
        "candidates": [c.to_agent_dict() for c in shortlist],
    }
    result = llm.complete_json(
        agent="scout",
        system=prompts.SCOUT,
        user=json.dumps(payload),
        ledger=ledger,
        max_tokens=3000,
    )
    valid_ids = {c.id for c in shortlist}
    out: dict[str, str] = {}
    for pick in result.get("picks") or []:
        pid, why = str(pick.get("id", "")), str(pick.get("why", "")).strip()
        # Silently drop any car the agent invented.
        if pid in valid_ids and why:
            out[pid] = why
    return out
