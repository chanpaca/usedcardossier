"""Thin LLM client with token accounting.

Every agent call goes through `LLMClient.complete`, which records prompt and
completion tokens. Those counters are what the report's Tokenomics section is
built from -- they are measured from real runs, not estimated.

Works against either the course Portkey gateway (gpt-6-luna) or a personal
OpenAI key, resolved by backend.config.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from openai import OpenAI

from .config import LLMConfig, load_llm_config

# Published per-million-token prices, used to turn measured token counts into
# dollars. Override via set_pricing() if the course publishes other numbers.
DEFAULT_PRICING_USD_PER_MTOK = {
    "gpt-6-luna": {"input": 0.15, "output": 0.60},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}

# gpt-6-luna is a REASONING model: the completion budget pays for internal
# reasoning tokens before a single character of answer is emitted. Measured on
# this app's dossier prompt, a 900-token cap produced 900 reasoning tokens and
# an EMPTY string; 3,000 produced 1,047 tokens and complete JSON. Budgets here
# are therefore sized for reasoning, not for the visible answer, and an empty
# response triggers one retry at double the cap.
DEFAULT_MAX_TOKENS = 3000
MIN_USEFUL_TOKENS = 1200


@dataclass
class CallRecord:
    agent: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    cached: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def cost_usd(self, pricing: dict[str, dict[str, float]]) -> float:
        rates = pricing.get(self.model) or {"input": 0.0, "output": 0.0}
        return (
            self.prompt_tokens * rates["input"]
            + self.completion_tokens * rates["output"]
        ) / 1_000_000


@dataclass
class UsageLedger:
    """Per-run token and cost accounting, surfaced in the UI and the report."""

    calls: list[CallRecord] = field(default_factory=list)
    pricing: dict[str, dict[str, float]] = field(
        default_factory=lambda: dict(DEFAULT_PRICING_USD_PER_MTOK)
    )

    def record(self, call: CallRecord) -> None:
        self.calls.append(call)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def total_cost_usd(self) -> float:
        return sum(c.cost_usd(self.pricing) for c in self.calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": [
                {**asdict(c), "cost_usd": round(c.cost_usd(self.pricing), 6)}
                for c in self.calls
            ],
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "call_count": len(self.calls),
        }


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or load_llm_config()
        kwargs: dict[str, Any] = {"api_key": self.config.api_key}
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        self._client = OpenAI(**kwargs)
        # Learned once per process from the first error, then reused so later
        # calls do not pay for the same failed round trip.
        self._token_param = "max_completion_tokens"

    def _adapt(self, kwargs: dict[str, Any], message: str) -> bool:
        """Drop or rename a parameter the endpoint rejected. True if changed."""
        text = message.lower()

        if "max_tokens" in text and "max_completion_tokens" in text:
            if "max_tokens" in kwargs:
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                self._token_param = "max_completion_tokens"
                return True

        if "max_completion_tokens" in text and "unsupported" in text:
            if "max_completion_tokens" in kwargs:
                kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
                self._token_param = "max_tokens"
                return True

        for param in ("temperature", "response_format"):
            if param in text and param in kwargs:
                kwargs.pop(param)
                return True

        return False

    def complete(
        self,
        *,
        agent: str,
        system: str,
        user: str,
        ledger: UsageLedger,
        temperature: float = 0.2,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        json_mode: bool = False,
        _retry_budget: int = 1,
    ) -> str:
        """One chat completion, with usage recorded into `ledger`.

        Gateways disagree about parameter names and defaults. Newer models
        behind the course Portkey gateway reject `max_tokens` in favour of
        `max_completion_tokens`, and some reject a non-default `temperature`.
        Rather than hard-code one dialect, unsupported parameters are dropped
        and the call retried, so the same code works on both the course key
        and a personal OpenAI key.
        """
        started = time.time()
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            self._token_param: max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = None
        last_error: Exception | None = None
        for _ in range(4):
            try:
                response = self._client.chat.completions.create(**kwargs)
                break
            except Exception as exc:  # noqa: BLE001 - inspected below
                last_error = exc
                dropped = self._adapt(kwargs, str(exc))
                if not dropped:
                    raise LLMError(f"{agent}: LLM call failed: {exc}") from exc

        if response is None:
            raise LLMError(f"{agent}: LLM call failed: {last_error}")

        usage = getattr(response, "usage", None)
        ledger.record(
            CallRecord(
                agent=agent,
                model=self.config.model,
                prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                latency_s=round(time.time() - started, 3),
            )
        )
        text = (response.choices[0].message.content or "").strip()

        # Empty output means reasoning consumed the whole budget. Retry once
        # with double the allowance rather than silently returning nothing.
        if not text and _retry_budget > 0:
            return self.complete(
                agent=agent,
                system=system,
                user=user,
                ledger=ledger,
                temperature=temperature,
                max_tokens=max_tokens * 2,
                json_mode=json_mode,
                _retry_budget=_retry_budget - 1,
            )
        return text

    def complete_json(self, **kwargs: Any) -> dict[str, Any]:
        """Completion constrained to JSON, parsed. Returns {} on bad output."""
        raw = self.complete(json_mode=True, **kwargs)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
