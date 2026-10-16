"""Measure real token usage and cost. Produces the numbers in the report.

Runs both flows against real vehicles and writes data/tokenomics.json with
per-agent token counts, latency and dollar cost, plus projections to 100 and
10,000 users.

    python -m scripts.measure_tokenomics

Needs a working LLM key. Vehicle data comes from the warm cache, so repeated
runs measure the same upstream data and the token figures are reproducible.
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import date

from backend.agents.team import BuyerProfile
from backend.config import DATA_DIR
from backend.llm import LLMClient
from backend.pipeline import Services, run_discovery, run_dossier_flow

OUT_PATH = DATA_DIR / "tokenomics.json"
AS_OF = date(2026, 10, 16)

DOSSIER_CASES = [
    {"year": 2018, "make": "Honda", "model": "Accord", "mileage": 78_000,
     "asking_price": 11_500, "vin": "1HGCV1F13JA000001"},
    {"year": 2018, "make": "Toyota", "model": "Camry", "mileage": 82_000,
     "asking_price": 13_900, "vin": None},
    {"year": 2017, "make": "Ford", "model": "F150 Pickup 4WD", "mileage": 95_000,
     "asking_price": 21_000, "vin": None},
]

DISCOVERY_CASES = [
    BuyerProfile(body_style="sedan", brand_slugs=["honda", "toyota", "mazda"],
                 priorities=["reliability", "running_cost"],
                 budget_min=10_000, budget_max=25_000, year_min=2016),
    BuyerProfile(body_style="suv", brand_slugs=[],
                 priorities=["safety", "comfort"],
                 budget_min=15_000, budget_max=40_000, year_min=2017),
]


def summarise(ledger_dict: dict) -> dict:
    by_agent: dict[str, dict] = {}
    for call in ledger_dict["calls"]:
        slot = by_agent.setdefault(
            call["agent"],
            {"calls": 0, "prompt": 0, "completion": 0, "latency": 0.0, "cost": 0.0},
        )
        slot["calls"] += 1
        slot["prompt"] += call["prompt_tokens"]
        slot["completion"] += call["completion_tokens"]
        slot["latency"] += call["latency_s"]
        slot["cost"] += call["cost_usd"]
    return by_agent


def main() -> int:
    svc = Services.build(LLMClient())
    runs: list[dict] = []

    print("=== DOSSIER RUNS ===")
    for case in DOSSIER_CASES:
        started = time.time()
        try:
            result = run_dossier_flow(svc, as_of=AS_OF, **case)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {case['make']} {case['model']}: {exc}")
            continue
        usage = result.usage.to_dict()
        runs.append({
            "flow": "dossier",
            "label": result.label,
            "wall_s": round(time.time() - started, 2),
            "usage": usage,
            "by_agent": summarise(usage),
            "critic_verdict": result.critic.verdict,
            "critic_issues": len(result.critic.issues),
            "critic_checked": result.critic.checked_count,
        })
        print(f"  {result.label:34s} {usage['total_tokens']:6,} tok  "
              f"${usage['total_cost_usd']:.5f}  {runs[-1]['wall_s']:5.1f}s  "
              f"critic={result.critic.verdict}")

    print("\n=== DISCOVERY RUNS ===")
    for profile in DISCOVERY_CASES:
        started = time.time()
        try:
            result = run_discovery(svc, profile, as_of=AS_OF)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {profile.body_style}: {exc}")
            continue
        usage = result.usage.to_dict()
        runs.append({
            "flow": "discovery",
            "label": f"{profile.body_style} / {','.join(profile.priorities)}",
            "wall_s": round(time.time() - started, 2),
            "usage": usage,
            "by_agent": summarise(usage),
            "results_returned": len(result.candidates),
            "filtered_from": result.filtered_count,
        })
        print(f"  {runs[-1]['label']:34s} {usage['total_tokens']:6,} tok  "
              f"${usage['total_cost_usd']:.5f}  {runs[-1]['wall_s']:5.1f}s  "
              f"{len(result.candidates)} results")

    if not runs:
        print("no successful runs")
        return 1

    def costs(flow: str) -> list[float]:
        return [r["usage"]["total_cost_usd"] for r in runs if r["flow"] == flow]

    def tokens(flow: str) -> list[int]:
        return [r["usage"]["total_tokens"] for r in runs if r["flow"] == flow]

    summary = {"measured_at": str(AS_OF), "runs": runs, "per_flow": {}}
    for flow in ("dossier", "discovery"):
        c, t = costs(flow), tokens(flow)
        if not c:
            continue
        summary["per_flow"][flow] = {
            "n": len(c),
            "mean_cost_usd": round(statistics.fmean(c), 6),
            "mean_tokens": round(statistics.fmean(t)),
            "mean_wall_s": round(
                statistics.fmean(
                    [r["wall_s"] for r in runs if r["flow"] == flow]
                ), 2),
        }

    # A realistic session: one discovery, then three dossiers on the shortlist.
    d = summary["per_flow"].get("discovery", {}).get("mean_cost_usd", 0)
    k = summary["per_flow"].get("dossier", {}).get("mean_cost_usd", 0)
    session = d + 3 * k
    summary["session"] = {
        "definition": "1 discovery + 3 dossiers",
        "cost_usd": round(session, 6),
        "at_100_users": round(session * 100, 2),
        "at_10k_users": round(session * 10_000, 2),
        "at_10k_users_monthly_2_sessions": round(session * 10_000 * 2, 2),
    }

    OUT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nsession (1 discovery + 3 dossiers) = ${session:.5f}")
    print(f"  100 users    ${summary['session']['at_100_users']:,.2f}")
    print(f"  10,000 users ${summary['session']['at_10k_users']:,.2f}")
    print(f"\nwrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
