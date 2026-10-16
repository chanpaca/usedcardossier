"""Configuration and environment loading.

The course requires the app to find a `.env` in two locations: the repo folder
and its parent. `load_environment` checks them in that order and stops at the
first file that exists, so a key dropped in either place works.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# Searched in order; the first file that exists wins.
ENV_SEARCH_PATHS = (REPO_ROOT / ".env", REPO_ROOT.parent / ".env")


def load_environment() -> Path | None:
    """Load the first `.env` found in the search path. Returns the file used."""
    for candidate in ENV_SEARCH_PATHS:
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return candidate
    return None


class ConfigError(RuntimeError):
    """Raised when the app cannot start because required config is missing."""


@dataclass(frozen=True)
class LLMConfig:
    """Which provider the agents talk to, resolved from the environment."""

    provider: str           # "portkey" | "openai"
    api_key: str
    model: str
    base_url: str | None

    @property
    def redacted_key(self) -> str:
        return f"{self.api_key[:6]}...{self.api_key[-4:]}" if len(self.api_key) > 12 else "..."


# The course gateway permits exactly one model. Pinning it here means a stale
# value left over from an earlier assignment cannot silently redirect traffic.
COURSE_MODEL = "gpt-6-luna"


def load_llm_config() -> LLMConfig:
    """Resolve LLM settings. Portkey wins if both keys are present."""
    portkey_key = os.getenv("PORTKEY_API_KEY", "").strip()
    if portkey_key and portkey_key != "your_key_here":
        model = os.getenv("PORTKEY_MODEL", COURSE_MODEL).strip() or COURSE_MODEL
        if model != COURSE_MODEL:
            raise ConfigError(
                f"PORTKEY_MODEL must be {COURSE_MODEL!r} on the course key, got {model!r}."
            )
        return LLMConfig(
            provider="portkey",
            api_key=portkey_key,
            model=model,
            base_url=os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1").strip(),
        )

    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key and openai_key != "your_key_here":
        return LLMConfig(
            provider="openai",
            api_key=openai_key,
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
            base_url=os.getenv("OPENAI_BASE_URL") or None,
        )

    raise ConfigError(
        "No LLM key found. Copy .env.example to .env and set PORTKEY_API_KEY "
        f"(or OPENAI_API_KEY). Searched: {', '.join(str(p) for p in ENV_SEARCH_PATHS)}"
    )


# --- data sources (keyless) -------------------------------------------------

FUELECONOMY_BASE_URL = os.getenv(
    "FUELECONOMY_BASE_URL", "https://www.fueleconomy.gov/ws/rest"
)
NHTSA_BASE_URL = os.getenv("NHTSA_BASE_URL", "https://api.nhtsa.gov")

DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
CACHE_DB = CACHE_DIR / "vehicle_cache.sqlite"
