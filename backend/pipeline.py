"""Orchestration: buyer answers in, analysed recommendations out.

Two entry points mirroring the two things a buyer does:

  run_discovery()  -- "what should I look at?"  funnel -> ranked shortlist
  run_dossier()    -- "should I buy THIS one?"  one car -> trims, defects, cost

Shared peer-group machinery lives here because both flows need the same thing:
to judge a vehicle against its contemporaries rather than in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import catalog
from .agents.dossier import (
    CriticReport,
    apply_critic_fixes,
    run_analyst,
    run_critic,
    run_dossier,
)
from .agents.team import (
    BuyerProfile,
    Candidate,
    ScoringSpec,
    run_profiler,
    run_scout,
    score_candidates,
)
from .config import CACHE_DB
from .depreciation import DepreciationModel
from .llm import LLMClient, UsageLedger
from .pricing import estimate_msrp, msrp_from_vin
from .risk import RiskSignal, complaint_risk
from .tco import compute_tco
from .tools.fueleconomy import FuelEconomyClient, Trim
from .tools.http_cache import HttpCache
from .tools.nhtsa import NhtsaClient, Recall
from .tools.vpic import VpicClient

# How many catalog rows survive the hard filter before per-vehicle API work
# starts. Each candidate costs several HTTP calls, so this bounds latency.
MAX_CANDIDATES_TO_ENRICH = 14
# Peer group: how many usable profiles we want, and how many candidates we are
# willing to try to get them. Many nameplates in a size class sell in small
# numbers, so the scan limit has to exceed the target comfortably.
MAX_PEERS = 10
PEER_SCAN_LIMIT = 70
MIN_PEER_COMPLAINTS = 20
# Typical mileage accumulation, used when the buyer has not named a car.
ASSUMED_MILES_PER_YEAR = 12_000


@dataclass
class Services:
    """Everything the pipeline needs. Built once, reused across requests."""

    cache: HttpCache
    fe: FuelEconomyClient
    nhtsa: NhtsaClient
    vpic: VpicClient
    depreciation: DepreciationModel
    llm: LLMClient | None = None

    @classmethod
    def build(cls, llm: LLMClient | None = None) -> Services:
        cache = HttpCache(CACHE_DB)
        return cls(
            cache=cache,
            fe=FuelEconomyClient(cache),
            nhtsa=NhtsaClient(cache),
            vpic=VpicClient(cache),
            depreciation=DepreciationModel(),
            llm=llm,
        )


# --- peer groups ------------------------------------------------------------


def peer_models(
    entry: catalog.CatalogEntry, limit: int = MAX_PEERS
) -> list[catalog.CatalogEntry]:
    """Same body style, same model year, different make. The comparison set."""
    peers = [
        e
        for e in catalog.load_catalog()
        if e.year == entry.year
        and e.body_style == entry.body_style
        and e.make != entry.make
    ]
    # Prefer the same EPA class for a tighter comparison, then fill out with
    # the rest of the body style.
    same_class = [e for e in peers if e.vehicle_class == entry.vehicle_class]
    rest = [e for e in peers if e.vehicle_class != entry.vehicle_class]

    def interleave(group: list[catalog.CatalogEntry]) -> list[catalog.CatalogEntry]:
        """Round-robin across makes.

        Sorting by make instead groups every model of one brand together, so
        the first N candidates cover only a handful of makes -- measured: a
        70-candidate scan yielded just 5 usable peers. Interleaving reaches
        one model from every make before taking a second from any.

        Mainstream brands go first within each round: they sell in volume and
        are far likelier to clear the complaint threshold.
        """
        by_make: dict[str, list[catalog.CatalogEntry]] = {}
        for item in group:
            by_make.setdefault(item.make, []).append(item)
        order = sorted(
            by_make,
            key=lambda m: (0 if by_make[m][0].tier == "mainstream" else 1, m),
        )
        out, depth = [], 0
        while True:
            added = False
            for make in order:
                bucket = by_make[make]
                if depth < len(bucket):
                    out.append(bucket[depth])
                    added = True
            if not added:
                break
            depth += 1
        return out

    return (interleave(same_class) + interleave(rest))[:limit]


def build_risk(
    svc: Services, entry: catalog.CatalogEntry
) -> tuple[RiskSignal, int]:
    """Complaint risk for one vehicle against its model-year peers.

    Peers are scanned until enough of them carry usable complaint volume, not
    merely until a fixed number of candidates has been tried. A first version
    stopped after 10 candidates, and because many nameplates in a size class
    sell in small numbers, often only three cleared the 20-complaint bar --
    below the minimum for a baseline, so the defect signal silently switched
    itself off on exactly the mainstream cars it matters most for.
    """
    mine = svc.nhtsa.complaints(entry.year, entry.make, entry.model)

    profiles: list[dict[str, int]] = []
    makes_used: set[str] = set()
    seen_models: set[str] = set()

    for peer in peer_models(entry, limit=PEER_SCAN_LIMIT):
        if len(profiles) >= MAX_PEERS:
            break
        # One profile per make, so a single prolific brand cannot define the
        # baseline on its own. Candidates are still scanned across every model
        # of that make until one carries usable volume.
        if peer.make in makes_used:
            continue
        key = f"{peer.make}|{peer.model}"
        if key in seen_models:
            continue
        seen_models.add(key)
        try:
            profile = svc.nhtsa.complaints(peer.year, peer.make, peer.model)
        except Exception:  # noqa: BLE001 - a peer failing must not fail the run
            continue
        if profile.total >= MIN_PEER_COMPLAINTS:
            profiles.append(profile.by_component)
            makes_used.add(peer.make)

    label = f"{entry.year} {entry.body_style}s"
    return complaint_risk(mine.by_component, profiles, segment_label=label), mine.total


def best_trim(trims: list[Trim]) -> Trim | None:
    """The trim used for discovery-level figures: cheapest to fuel."""
    with_cost = [t for t in trims if t.annual_fuel_cost]
    if with_cost:
        return min(with_cost, key=lambda t: t.annual_fuel_cost or 0)
    return trims[0] if trims else None


def ncap_overall(svc: Services, entry: catalog.CatalogEntry) -> str | None:
    try:
        ratings = svc.nhtsa.safety_ratings(entry.year, entry.make, entry.model)
    except Exception:  # noqa: BLE001
        return None
    stars = [r.overall for r in ratings if r.overall and r.overall.isdigit()]
    return max(stars) if stars else None


# --- discovery --------------------------------------------------------------


@dataclass
class DiscoveryResult:
    spec: ScoringSpec
    candidates: list[Candidate]
    rationales: dict[str, str]
    usage: UsageLedger
    filtered_count: int
    enriched_count: int
    notes: list[str] = field(default_factory=list)
    # Diagnostics only. Exposed in the API for debugging, not rendered as prose.
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": {k: round(v, 3) for k, v in self.spec.normalised().items()},
            "tensions": self.spec.tensions,
            "profiler_notes": self.spec.notes,
            "filtered_count": self.filtered_count,
            "enriched_count": self.enriched_count,
            "notes": self.notes,
            "skipped": self.skipped,
            "results": [
                {**c.to_dict(), "why": self.rationales.get(c.id, "")}
                for c in self.candidates
            ],
            "usage": self.usage.to_dict(),
        }


def run_discovery(
    svc: Services, profile: BuyerProfile, *, as_of: date | None = None
) -> DiscoveryResult:
    today = as_of or date.today()
    ledger = UsageLedger()
    notes: list[str] = []
    skipped: list[str] = []

    # 1. Profiler turns stated priorities into weights.
    if svc.llm:
        spec = run_profiler(svc.llm, profile, ledger)
    else:
        spec = ScoringSpec.fallback("no LLM configured")

    # 2. Hard filter in Python. Agents never see a car that fails this.
    matches = catalog.search(
        body_style=profile.body_style,
        brand_slugs=profile.brand_slugs,
        year_min=profile.year_min,
        year_max=profile.year_max,
    )
    filtered_count = len(matches)

    # Spread picks across years, not just the newest.
    #
    # Taking the first N of a year-sorted list produced a shortlist that was
    # entirely 2021 cars, every one of them near the top of the budget. Older
    # model years are usually the better value and are exactly what a used
    # buyer is weighing, so candidates are drawn round-robin across the
    # available years, one nameplate each.
    # Buckets are keyed by (year, make) so the round robin spreads over both.
    # Keying on year alone still returned eight Hondas, because within a year
    # the catalog is sorted by make and Honda comes first.
    buckets: dict[tuple[int, str], list[catalog.CatalogEntry]] = {}
    seen: set[tuple[str, str]] = set()
    for entry in matches:
        key = (entry.make, entry.model)
        if key in seen:
            continue
        seen.add(key)
        buckets.setdefault((entry.year, entry.make), []).append(entry)

    # Newest year first, but makes interleaved within each year.
    ordered_keys = sorted(buckets, key=lambda k: (-k[0], k[1]))
    picked: list[catalog.CatalogEntry] = []
    round_index = 0
    while ordered_keys and len(picked) < MAX_CANDIDATES_TO_ENRICH:
        progressed = False
        for key in ordered_keys:
            bucket = buckets[key]
            if round_index < len(bucket):
                picked.append(bucket[round_index])
                progressed = True
                if len(picked) >= MAX_CANDIDATES_TO_ENRICH:
                    break
        if not progressed:
            break
        round_index += 1

    # 3. Enrich each survivor with real data and a deterministic cost.
    candidates: list[Candidate] = []
    for entry in picked:
        try:
            candidate = _enrich(svc, entry, profile, today)
        except Exception as exc:  # noqa: BLE001 - one bad car must not fail all
            # Kept for diagnostics, but not shown as prose. These are almost
            # always an EPA nameplate NHTSA does not index; a wall of raw URLs
            # is noise to a car buyer.
            skipped.append(f"{entry.label}: {exc}")
            continue
        if candidate is None:
            continue
        if not (profile.budget_min <= candidate.value_point <= profile.budget_max):
            continue
        candidates.append(candidate)

    if not candidates:
        notes.append(
            "No vehicles matched that combination of body style, brands and "
            "budget. Try widening the budget or adding brands."
        )
    elif skipped:
        # One quiet line, not a wall of URLs.
        notes.append(
            f"{len(skipped)} candidate(s) had no NHTSA record under their EPA "
            f"name and were left out."
        )

    # 4. Rank in Python using the Profiler's weights.
    score_candidates(candidates, spec)
    top = candidates[:8]

    # 5. Scout explains the ranking. It cannot change it.
    rationales = run_scout(svc.llm, top, profile, spec, ledger) if svc.llm else {}

    return DiscoveryResult(
        spec=spec,
        candidates=top,
        rationales=rationales,
        usage=ledger,
        filtered_count=filtered_count,
        enriched_count=len(candidates),
        notes=notes,
        skipped=skipped,
    )


def _enrich(
    svc: Services,
    entry: catalog.CatalogEntry,
    profile: BuyerProfile,
    today: date,
) -> Candidate | None:
    trims = svc.fe.trims(entry.year, entry.make, entry.model)
    trim = best_trim(trims)
    if trim is None:
        return None

    msrp = estimate_msrp(entry.make, entry.model, entry.body_style)
    age = max(0, today.year - entry.year)
    mileage = age * ASSUMED_MILES_PER_YEAR

    value = svc.depreciation.estimate(
        msrp.value, entry.year, mileage, entry.vehicle_class, as_of=today
    )
    resale = svc.depreciation.resale_after(
        msrp.value,
        entry.year,
        mileage,
        entry.vehicle_class,
        years_held=profile.hold_years,
        annual_miles=profile.annual_miles,
        as_of=today,
    )
    risk, _ = build_risk(svc, entry)
    tco = compute_tco(
        purchase_price=value.point,
        projected_resale=resale,
        msrp=msrp.value,
        vehicle_age_at_purchase=age,
        epa_annual_fuel_cost=trim.annual_fuel_cost,
        complaint_risk_ratio=risk.cost_ratio,
        hold_years=profile.hold_years,
        annual_miles=profile.annual_miles,
    )
    return Candidate(
        entry=entry,
        value_point=value.point,
        value_low=value.low,
        value_high=value.high,
        tco=tco,
        risk=risk,
        annual_fuel_cost=trim.annual_fuel_cost,
        ncap_overall=ncap_overall(svc, entry),
        msrp_confidence=msrp.confidence,
    )


# --- dossier ----------------------------------------------------------------


@dataclass
class DossierResult:
    label: str
    trims: list[Trim]
    recalls: list[Recall]
    risk: RiskSignal
    tco: Any
    msrp: Any
    value: Any
    dossier: dict[str, Any]
    analysis: dict[str, Any]
    critic: CriticReport
    usage: UsageLedger
    ncap: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "msrp": self.msrp.to_dict(),
            "value": {
                "low": self.value.low,
                "point": self.value.point,
                "high": self.value.high,
                "retention": self.value.retention,
                "median_abs_pct_error": self.value.median_abs_pct_error,
            },
            "ncap_overall": self.ncap,
            "trims": [t.to_dict() for t in self.trims],
            "recalls": [
                {
                    "campaign": r.campaign_number,
                    "component": r.component,
                    "summary": r.summary,
                    "consequence": r.consequence,
                    "remedy": r.remedy,
                    "report_date": r.report_date,
                    "park_it": r.park_it,
                    "url": r.citation_url,
                }
                for r in self.recalls
            ],
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
            "tco": self.tco.to_dict(),
            "dossier": self.dossier,
            "analysis": self.analysis,
            "critic": self.critic.to_dict(),
            "usage": self.usage.to_dict(),
        }


def run_dossier_flow(
    svc: Services,
    *,
    year: int,
    make: str,
    model: str,
    mileage: int | None = None,
    asking_price: int | None = None,
    vin: str | None = None,
    annual_miles: int = ASSUMED_MILES_PER_YEAR,
    hold_years: int = 5,
    as_of: date | None = None,
) -> DossierResult:
    today = as_of or date.today()
    ledger = UsageLedger()
    label = f"{year} {make} {model}"

    trims = svc.fe.trims(year, make, model)
    recalls = svc.nhtsa.recalls(year, make, model)

    entry = next(
        (
            e
            for e in catalog.load_catalog()
            if e.year == year and e.make == make and e.model == model
        ),
        None,
    )
    if entry is None:
        klass = trims[0].vehicle_class if trims else ""
        entry = catalog.CatalogEntry(
            year=year,
            make=make,
            model=model,
            brand_slug=make.lower(),
            tier="mainstream",
            vehicle_class=klass,
            body_style=catalog.classify_body(klass) or "sedan",
        )

    risk, _ = build_risk(svc, entry)

    # MSRP: exact from the VIN when we have one, estimated otherwise.
    msrp = None
    if vin:
        info = svc.vpic.decode_vin(vin, year)
        if info:
            msrp = msrp_from_vin(info.base_price)
    if msrp is None:
        msrp = estimate_msrp(make, model, entry.body_style)

    age = max(0, today.year - year)
    miles = mileage if mileage is not None else age * ASSUMED_MILES_PER_YEAR
    value = svc.depreciation.estimate(
        msrp.value, year, miles, entry.vehicle_class, as_of=today
    )
    # The buyer's own asking price always beats our model.
    purchase_price = asking_price or value.point
    resale = svc.depreciation.resale_after(
        msrp.value,
        year,
        miles,
        entry.vehicle_class,
        years_held=hold_years,
        annual_miles=annual_miles,
        as_of=today,
    )
    trim = best_trim(trims)
    tco = compute_tco(
        purchase_price=purchase_price,
        projected_resale=resale,
        msrp=msrp.value,
        vehicle_age_at_purchase=age,
        epa_annual_fuel_cost=trim.annual_fuel_cost if trim else None,
        complaint_risk_ratio=risk.cost_ratio,
        hold_years=hold_years,
        annual_miles=annual_miles,
    )

    dossier_out: dict[str, Any] = {}
    analysis_out: dict[str, Any] = {}
    critic = CriticReport(verdict="pass")

    if svc.llm:
        dossier_out = run_dossier(
            svc.llm,
            label=label,
            trims=trims,
            recalls=recalls,
            risk=risk,
            ledger=ledger,
        )
        analysis_out = run_analyst(svc.llm, label=label, tco=tco, ledger=ledger)
        critic = run_critic(
            svc.llm,
            label=label,
            dossier=dossier_out,
            analysis=analysis_out,
            trims=trims,
            recalls=recalls,
            risk=risk,
            tco=tco,
            ledger=ledger,
        )
        dossier_out, analysis_out = apply_critic_fixes(
            dossier_out, analysis_out, critic
        )

    return DossierResult(
        label=label,
        trims=trims,
        recalls=recalls,
        risk=risk,
        tco=tco,
        msrp=msrp,
        value=value,
        dossier=dossier_out,
        analysis=analysis_out,
        critic=critic,
        usage=ledger,
        ncap=ncap_overall(svc, entry),
    )
