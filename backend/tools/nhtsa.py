"""Clients for the NHTSA public APIs (free, keyless, US-only).

Three endpoints supply the defect layer of the dossier:

  * recalls      -- open safety recall campaigns for a year/make/model
  * complaints   -- owner-filed complaints, bucketed by component
  * SafetyRatings-- NCAP crash-test stars (two calls: look up id, then fetch)

Every defect claim the app makes is traceable to one of these, which is what
lets the Critic agent reject uncited assertions.

API docs: https://www.nhtsa.gov/nhtsa-datasets-and-apis
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..config import NHTSA_BASE_URL
from .http_cache import HttpCache


@dataclass(frozen=True)
class Recall:
    campaign_number: str       # the citable NHTSA id, e.g. "20V314000"
    component: str
    summary: str
    consequence: str
    remedy: str
    report_date: str
    park_it: bool              # fire risk; do not drive
    park_outside: bool

    @property
    def citation_url(self) -> str:
        return f"https://www.nhtsa.gov/recalls?nhtsaId={self.campaign_number}"


@dataclass(frozen=True)
class ComplaintProfile:
    """Aggregate view of owner complaints for one year/make/model."""

    total: int
    by_component: dict[str, int] = field(default_factory=dict)
    with_crash: int = 0
    with_fire: int = 0
    with_injury: int = 0

    def top_components(self, limit: int = 6) -> list[tuple[str, int]]:
        return sorted(self.by_component.items(), key=lambda kv: -kv[1])[:limit]


@dataclass(frozen=True)
class SafetyRating:
    description: str
    overall: str | None
    frontal_crash: str | None
    side_crash: str | None
    rollover: str | None


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "yes", "1"}


# EPA and NHTSA do not agree on model names. EPA distinguishes body styles and
# trim packages ("Civic 4Dr", "Accord Sport/Touring", "Avalon Hybrid XLE");
# NHTSA indexes the nameplate only ("Civic", "Accord", "Avalon") and answers
# HTTP 400 for anything else. Measured: 8 of 14 candidates in one discovery run
# were dropped on 400 before this normalisation existed.
_ENGINE_TOKEN = re.compile(r"\d+\.\d+[TL]?|V\d|I\d", re.I)

_TRIM_SUFFIXES = {
    "2dr", "4dr", "5dr", "2door", "4door", "5door", "door",
    "awd", "fwd", "rwd", "4wd", "2wd",
    "xle", "xse", "xlt", "se", "le", "sel", "ltd", "limited", "touring",
    "sport", "premium", "platinum", "base", "eco", "ecoboost",
    "coupe", "sedan", "wagon", "hatchback", "convertible",
    "cab", "crew", "pickup", "van",
}


# Nameplates where EPA and NHTSA simply spell the same vehicle differently.
# Applied after suffix stripping, matched case-insensitively.
_MODEL_ALIASES = {
    "f150": "F-150", "f250": "F-250", "f350": "F-350",
    "c1500": "C1500", "k1500": "K1500",
    "ram 1500": "1500", "ram 2500": "2500", "ram 3500": "3500",
    "cx5": "CX-5", "cx9": "CX-9", "cx3": "CX-3", "cx30": "CX-30",
    "mx5": "MX-5", "mazda3": "MAZDA3", "mazda6": "MAZDA6",
    "mazda 3": "MAZDA3", "mazda 6": "MAZDA6", "mazda 2": "MAZDA2",
    "3": "MAZDA3", "6": "MAZDA6", "2": "MAZDA2",
    "crv": "CR-V", "hrv": "HR-V", "brv": "BR-V",
}


def normalise_model(model: str) -> str:
    """Reduce an EPA model string to the nameplate NHTSA will recognise.

    "Accord Sport/Touring" -> "Accord"      (slash means a trim pairing)
    "Civic 4Dr"            -> "Civic"       (body style suffix)
    "Avalon Hybrid XLE"    -> "Avalon Hybrid" (keep Hybrid: a distinct model)

    Hybrid, Plug-in and similar powertrain words are kept because NHTSA does
    treat them as separate models. Only body-style and trim-level words are
    stripped, and never the first word.
    """
    text = (model or "").split("/")[0].split(" - ")[0].strip()
    words = []
    for word in text.split():
        # Engine designations ("2.0T", "1.5L", "V6") are EPA detail that NHTSA
        # does not index. Stop at the first one.
        if _ENGINE_TOKEN.fullmatch(word):
            break
        words.append(word)
    while len(words) > 1 and words[-1].lower().replace("-", "").strip(".") in _TRIM_SUFFIXES:
        words.pop()
    name = " ".join(words) or (text.split()[0] if text else text)
    return _MODEL_ALIASES.get(name.lower(), name)


def _normalise_component(raw: str) -> str:
    """NHTSA packs several components into one comma-joined string.

    Keep only the top-level category before the first colon so that
    "FUEL SYSTEM, GASOLINE:DELIVERY:FUEL PUMP" and
    "FUEL SYSTEM, GASOLINE:STORAGE" aggregate together.
    """
    head = (raw or "UNKNOWN").split(":")[0].strip().upper()
    return head or "UNKNOWN"


class NhtsaClient:
    def __init__(self, cache: HttpCache, base_url: str = NHTSA_BASE_URL) -> None:
        self.cache = cache
        self.base_url = base_url

    # -- recalls -------------------------------------------------------------

    def recalls(self, year: int, make: str, model: str) -> list[Recall]:
        payload = self.cache.get_json(
            self.base_url,
            "/recalls/recallsByVehicle",
            {"make": make, "model": normalise_model(model), "modelYear": year},
        )
        results = (payload or {}).get("results") or []
        return [
            Recall(
                campaign_number=item.get("NHTSACampaignNumber", ""),
                component=item.get("Component", ""),
                summary=item.get("Summary", ""),
                consequence=item.get("Consequence", ""),
                remedy=item.get("Remedy", ""),
                report_date=item.get("ReportReceivedDate", ""),
                park_it=_truthy(item.get("parkIt")),
                park_outside=_truthy(item.get("parkOutSide")),
            )
            for item in results
        ]

    # -- complaints ----------------------------------------------------------

    def complaints(self, year: int, make: str, model: str) -> ComplaintProfile:
        payload = self.cache.get_json(
            self.base_url,
            "/complaints/complaintsByVehicle",
            {"make": make, "model": normalise_model(model), "modelYear": year},
        )
        results = (payload or {}).get("results") or []
        components: Counter[str] = Counter()
        crash = fire = injury = 0
        for item in results:
            components[_normalise_component(item.get("components", ""))] += 1
            if _truthy(item.get("crash")):
                crash += 1
            if _truthy(item.get("fire")):
                fire += 1
            if int(item.get("numberOfInjuries") or 0) > 0:
                injury += 1
        return ComplaintProfile(
            total=len(results),
            by_component=dict(components),
            with_crash=crash,
            with_fire=fire,
            with_injury=injury,
        )

    # -- NCAP crash ratings --------------------------------------------------

    def safety_ratings(self, year: int, make: str, model: str) -> list[SafetyRating]:
        """NCAP stars. Needs two calls: resolve body styles, then fetch each."""
        index = self.cache.get_json(
            self.base_url,
            f"/SafetyRatings/modelyear/{year}/make/{make}/model/{normalise_model(model)}",
        )
        ratings = []
        for entry in (index or {}).get("Results") or []:
            vehicle_id = entry.get("VehicleId")
            if not vehicle_id:
                continue
            detail = self.cache.get_json(
                self.base_url, f"/SafetyRatings/VehicleId/{vehicle_id}"
            )
            for row in (detail or {}).get("Results") or []:
                ratings.append(
                    SafetyRating(
                        description=row.get("VehicleDescription", ""),
                        overall=row.get("OverallRating") or None,
                        frontal_crash=row.get("OverallFrontCrashRating") or None,
                        side_crash=row.get("OverallSideCrashRating") or None,
                        rollover=row.get("RolloverRating") or None,
                    )
                )
        return ratings
