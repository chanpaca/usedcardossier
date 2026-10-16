"""Turn NHTSA complaint data into a volume-independent risk signal.

Why not just count complaints
-----------------------------
A raw complaint count measures popularity at least as much as quality. Honda
sold vastly more 2018 Accords than Buick sold Regals, so the Accord will always
show more complaints. Ranking on raw counts systematically punishes the
high-volume, widely-recommended cars most buyers are actually considering.

Comparing a count against a peer-group median does not fix this, because the
peers themselves have wildly different sales volumes -- the median just tracks
whichever models happened to sell in modest numbers. We measured this: against
a 2018 midsize-sedan peer median of 229, the Accord scored 4.0x and took the
maximum risk penalty, while the Kia Optima looked clean.

What is used instead
--------------------
COMPONENT SHARE. For each vehicle, what fraction of its complaints concern a
given system, compared with the same fraction across its segment. A share is a
ratio of two counts for the same car, so sales volume cancels out entirely.

This inverts the earlier result, and the inversion is independently verifiable:

    2018 midsize sedans, ENGINE share vs segment baseline of 15.0 percent
      Honda Accord    13.2 percent   0.88x   (below average)
      Toyota Camry     4.7 percent   0.31x
      Hyundai Sonata  31.8 percent   2.12x
      Kia Optima      37.5 percent   2.50x

The method surfaces the Hyundai/Kia Theta II engine-failure problem -- a real,
documented, large-scale defect episode -- from public complaint data alone,
without being told to look for it. The Accord, which the count-based method
flagged hardest, is below its segment average.

Limits, stated plainly: this detects a model that is unusual AMONG ITS
CONTEMPORARIES. It cannot detect a defect shared by an entire segment, and a
vehicle with very few total complaints yields an unstable share.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Below this many complaints, shares are too noisy to interpret.
MIN_COMPLAINTS_FOR_SHARE = 25
# Below this many peer models, the segment baseline is unreliable.
MIN_PEERS = 4
# A component needs this many complaints before it can be called a concern.
MIN_COMPONENT_COMPLAINTS = 10

# Systems whose failure is expensive or dangerous. Matched as keywords because
# NHTSA packs several components into one comma-joined string.
CRITICAL_SYSTEMS = {
    "ENGINE": ("ENGINE",),
    "TRANSMISSION": ("POWER TRAIN", "TRANSMISSION"),
    "BRAKES": ("SERVICE BRAKES",),
    "STEERING": ("STEERING",),
    "ELECTRICAL": ("ELECTRICAL SYSTEM",),
    "FUEL": ("FUEL SYSTEM", "FUEL/PROPULSION"),
    "AIRBAGS": ("AIR BAGS",),
}


@dataclass(frozen=True)
class SystemFlag:
    """One system where this vehicle stands out against its segment."""

    system: str
    complaints: int
    share: float           # share of this vehicle's complaints
    segment_share: float   # same share across the segment
    ratio: float           # share / segment_share

    @property
    def severity(self) -> str:
        if self.ratio >= 2.0:
            return "high"
        return "elevated" if self.ratio >= 1.4 else "normal"

    @property
    def summary(self) -> str:
        return (
            f"{self.system}: {self.complaints:,} complaints, "
            f"{self.share:.1%} of this vehicle's total versus "
            f"{self.segment_share:.1%} across the segment ({self.ratio:.2f}x)"
        )


@dataclass(frozen=True)
class RiskSignal:
    total_complaints: int
    flags: tuple[SystemFlag, ...] = field(default_factory=tuple)
    peer_count: int = 0
    confidence: str = "none"          # "good" | "weak" | "none"
    explanation: str = ""

    @property
    def worst(self) -> SystemFlag | None:
        return max(self.flags, key=lambda f: f.ratio) if self.flags else None

    @property
    def concerns(self) -> tuple[SystemFlag, ...]:
        return tuple(f for f in self.flags if f.severity in {"high", "elevated"})

    @property
    def cost_ratio(self) -> float:
        """Maintenance multiplier input. 1.0 == segment-typical.

        Driven by the worst critical system rather than the total, and
        deliberately damped: complaint data is self-reported and one viral
        issue can inflate a share beyond its true cost impact.
        """
        if self.confidence == "none" or not self.flags:
            return 1.0
        worst = self.worst
        damping = 0.5 if self.confidence == "good" else 0.25
        return round(max(0.8, min(1.8, 1.0 + (worst.ratio - 1.0) * damping)), 3)


def _match_system(component: str) -> str | None:
    text = (component or "").upper()
    for system, keywords in CRITICAL_SYSTEMS.items():
        if any(keyword in text for keyword in keywords):
            return system
    return None


def _system_totals(by_component: dict[str, int]) -> dict[str, int]:
    """Collapse NHTSA's component strings onto critical systems."""
    totals: dict[str, int] = {}
    for component, count in by_component.items():
        system = _match_system(component)
        if system:
            totals[system] = totals.get(system, 0) + count
    return totals


def segment_baseline(peer_profiles: list[dict[str, int]]) -> dict[str, float]:
    """Pooled share of each critical system across a peer group."""
    pooled: dict[str, int] = {}
    grand_total = 0
    for profile in peer_profiles:
        grand_total += sum(profile.values())
        for system, count in _system_totals(profile).items():
            pooled[system] = pooled.get(system, 0) + count
    if grand_total <= 0:
        return {}
    return {system: count / grand_total for system, count in pooled.items()}


def complaint_risk(
    by_component: dict[str, int],
    peer_profiles: list[dict[str, int]],
    *,
    segment_label: str = "its segment",
) -> RiskSignal:
    """Score one vehicle's complaint mix against its segment."""
    total = sum(by_component.values())

    if len(peer_profiles) < MIN_PEERS:
        return RiskSignal(
            total_complaints=total,
            peer_count=len(peer_profiles),
            confidence="none",
            explanation=(
                f"Only {len(peer_profiles)} comparable vehicles found in "
                f"{segment_label}; too few for a segment baseline. No risk "
                f"adjustment applied."
            ),
        )

    if total < MIN_COMPLAINTS_FOR_SHARE:
        return RiskSignal(
            total_complaints=total,
            peer_count=len(peer_profiles),
            confidence="none",
            explanation=(
                f"Only {total} complaints on record, too few for a stable "
                f"breakdown by system. No risk adjustment applied."
            ),
        )

    baseline = segment_baseline(peer_profiles)
    mine = _system_totals(by_component)

    flags = []
    for system, count in mine.items():
        seg_share = baseline.get(system, 0.0)
        if count < MIN_COMPONENT_COMPLAINTS or seg_share <= 0:
            continue
        share = count / total
        flags.append(
            SystemFlag(
                system=system,
                complaints=count,
                share=share,
                segment_share=seg_share,
                ratio=round(max(0.1, min(5.0, share / seg_share)), 3),
            )
        )
    flags.sort(key=lambda f: -f.ratio)

    confidence = "good" if len(peer_profiles) >= 8 and total >= 100 else "weak"
    concerns = [f for f in flags if f.severity in {"high", "elevated"}]
    if concerns:
        detail = "; ".join(f.summary for f in concerns[:3])
        explanation = (
            f"{total:,} complaints across {len(peer_profiles)} comparable "
            f"{segment_label}. Standing out: {detail}."
        )
    else:
        explanation = (
            f"{total:,} complaints, but the mix across systems is "
            f"unremarkable for {segment_label}. No system stands out."
        )

    return RiskSignal(
        total_complaints=total,
        flags=tuple(flags),
        peer_count=len(peer_profiles),
        confidence=confidence,
        explanation=explanation,
    )
