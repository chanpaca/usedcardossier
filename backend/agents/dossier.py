"""Dossier workflow: Dossier -> Analyst -> Critic.

The Critic is the safety mechanism. It receives both the prose the other two
agents wrote and the source data they were supposed to draw from, and checks
each claim against it. High-severity issues are applied automatically before
the user sees anything; the full issue list is returned either way so the UI
can show that the check ran and what it caught.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..llm import LLMClient, UsageLedger
from ..risk import RiskSignal
from ..tco import TcoResult
from ..tools.fueleconomy import Trim
from ..tools.nhtsa import Recall
from . import prompts

MAX_RECALLS_TO_AGENT = 8
MAX_RECALL_SUMMARY_CHARS = 240


@dataclass
class CriticIssue:
    severity: str
    claim: str
    problem: str
    fix: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "claim": self.claim,
            "problem": self.problem,
            "fix": self.fix,
        }


@dataclass
class CriticReport:
    verdict: str                    # "pass" | "revise"
    issues: list[CriticIssue] = field(default_factory=list)
    checked_count: int = 0
    applied: list[str] = field(default_factory=list)

    @property
    def high_severity(self) -> list[CriticIssue]:
        return [i for i in self.issues if i.severity == "high"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "checked_count": self.checked_count,
            "issue_count": len(self.issues),
            "high_severity_count": len(self.high_severity),
            "issues": [i.to_dict() for i in self.issues],
            "applied": self.applied,
        }


def _trims_for_agent(trims: list[Trim]) -> list[dict[str, Any]]:
    return [
        {
            "trim": t.label,
            "transmission": t.transmission,
            "engine": f"{t.cylinders} cyl {t.displacement_l}L" if t.cylinders else "",
            "drive": t.drive,
            "mpg_combined": t.mpg_combined,
            "annual_fuel_cost_usd": t.annual_fuel_cost,
        }
        for t in trims
    ]


def _recalls_for_agent(recalls: list[Recall]) -> list[dict[str, Any]]:
    return [
        {
            "campaign": r.campaign_number,
            "component": r.component,
            "summary": r.summary[:MAX_RECALL_SUMMARY_CHARS],
            "park_it": r.park_it,
        }
        for r in recalls[:MAX_RECALLS_TO_AGENT]
    ]


def _risk_for_agent(risk: RiskSignal) -> dict[str, Any]:
    return {
        "total_complaints": risk.total_complaints,
        "confidence": risk.confidence,
        "flagged_systems": [
            {
                "system": f.system,
                "complaints": f.complaints,
                "share_pct": round(f.share * 100, 1),
                "segment_share_pct": round(f.segment_share * 100, 1),
                "ratio": f.ratio,
                "severity": f.severity,
            }
            for f in risk.flags
        ],
    }


def _tco_for_agent(tco: TcoResult) -> dict[str, Any]:
    return {
        "purchase_price_usd": tco.purchase_price,
        "projected_resale_usd": tco.projected_resale,
        "hold_years": tco.hold_years,
        "annual_miles": tco.annual_miles,
        "total_usd": tco.total,
        "per_year_usd": tco.per_year,
        "lines": [
            {
                "label": line.label,
                "total_usd": line.total,
                "per_year_usd": line.per_year,
                "basis": line.basis,
                "rests_on_assumption": line.is_modelled,
            }
            for line in tco.lines
        ],
        "warnings": list(tco.warnings),
    }


# --- agent 3: Dossier -------------------------------------------------------


def run_dossier(
    llm: LLMClient,
    *,
    label: str,
    trims: list[Trim],
    recalls: list[Recall],
    risk: RiskSignal,
    ledger: UsageLedger,
) -> dict[str, Any]:
    payload = {
        "vehicle": label,
        "trims": _trims_for_agent(trims),
        "nhtsa_recalls": _recalls_for_agent(recalls),
        "nhtsa_complaints": _risk_for_agent(risk),
    }
    return llm.complete_json(
        agent="dossier",
        system=prompts.DOSSIER,
        user=json.dumps(payload),
        ledger=ledger,
        max_tokens=4000,
    )


# --- agent 4: Analyst -------------------------------------------------------


def run_analyst(
    llm: LLMClient, *, label: str, tco: TcoResult, ledger: UsageLedger
) -> dict[str, Any]:
    payload = {"vehicle": label, "cost_table": _tco_for_agent(tco)}
    return llm.complete_json(
        agent="analyst",
        system=prompts.ANALYST,
        user=json.dumps(payload),
        ledger=ledger,
        max_tokens=2500,
    )


# --- agent 5: Critic --------------------------------------------------------


def run_critic(
    llm: LLMClient,
    *,
    label: str,
    dossier: dict[str, Any],
    analysis: dict[str, Any],
    trims: list[Trim],
    recalls: list[Recall],
    risk: RiskSignal,
    tco: TcoResult,
    ledger: UsageLedger,
) -> CriticReport:
    payload = {
        "vehicle": label,
        "agent_output_to_check": {"dossier": dossier, "cost_analysis": analysis},
        "source_data": {
            "trims": _trims_for_agent(trims),
            "nhtsa_recalls": _recalls_for_agent(recalls),
            "nhtsa_complaints": _risk_for_agent(risk),
            "cost_table": _tco_for_agent(tco),
        },
    }
    result = llm.complete_json(
        agent="critic",
        system=prompts.CRITIC,
        user=json.dumps(payload),
        ledger=ledger,
        max_tokens=4000,
    )

    issues = []
    for raw in result.get("issues") or []:
        severity = str(raw.get("severity", "low")).lower()
        if severity not in {"high", "medium", "low"}:
            severity = "low"
        issues.append(
            CriticIssue(
                severity=severity,
                claim=str(raw.get("claim", ""))[:400],
                problem=str(raw.get("problem", ""))[:400],
                fix=str(raw.get("fix", ""))[:400],
            )
        )

    verdict = str(result.get("verdict", "")).lower()
    if verdict not in {"pass", "revise"}:
        verdict = "revise" if issues else "pass"

    try:
        checked = int(result.get("checked_count") or 0)
    except (TypeError, ValueError):
        checked = 0

    return CriticReport(verdict=verdict, issues=issues, checked_count=checked)


def apply_critic_fixes(
    dossier: dict[str, Any], analysis: dict[str, Any], report: CriticReport
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply high-severity corrections before the user sees anything.

    A high-severity issue means an uncitable defect claim or a wrong dollar
    figure, so it is removed or replaced rather than merely annotated. Medium
    and low issues are surfaced in the UI but left in place, because
    over-eager automatic rewriting is its own failure mode.
    """
    dossier = json.loads(json.dumps(dossier))
    analysis = json.loads(json.dumps(analysis))

    def scrub(blob: dict[str, Any], bad: str, fix: str) -> bool:
        changed = False
        for key, value in list(blob.items()):
            if isinstance(value, str) and bad and bad in value:
                blob[key] = value.replace(bad, fix) if fix else ""
                changed = True
            elif isinstance(value, list):
                new_list = []
                for item in value:
                    if isinstance(item, str) and bad and bad in item:
                        changed = True
                        if fix:
                            new_list.append(item.replace(bad, fix))
                        continue
                    if isinstance(item, dict):
                        changed |= scrub(item, bad, fix)
                    new_list.append(item)
                blob[key] = new_list
        return changed

    for issue in report.high_severity:
        hit = scrub(dossier, issue.claim, issue.fix)
        hit |= scrub(analysis, issue.claim, issue.fix)
        if hit:
            report.applied.append(
                f"[{issue.severity}] removed or corrected: {issue.problem[:120]}"
            )

    return dossier, analysis
