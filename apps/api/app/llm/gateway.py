"""
LLM gateway and model router (spec section 37).

Two rules encoded here:

1. The application never imports a provider SDK directly. Everything goes through
   ``LLMGateway.complete`` so the provider can be swapped, budgeted, traced and
   rate-limited in one place.
2. The system degrades rather than fails when no provider is configured. Every
   number InsightOS reports is computed in Python; the model only writes prose.
   With no API key you still get the full investigation and a templated
   narrative -- which is also how the deterministic evaluation suite runs.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.config import settings

log = logging.getLogger(__name__)


class TaskKind(StrEnum):
    PLANNING = "planning"
    NARRATIVE = "narrative"
    SQL_REPAIR = "sql_repair"
    CLASSIFICATION = "classification"


@dataclass(slots=True)
class LLMResponse:
    text: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    degraded: bool = False       # True when produced without a live provider

    @property
    def estimated_cost_usd(self) -> float:
        # Published Sonnet-class pricing; override per-provider in production.
        return (self.input_tokens * 3.0 + self.output_tokens * 15.0) / 1_000_000


@dataclass
class UsageLedger:
    """Per-investigation cost and latency accounting, surfaced in observability."""
    calls: list[LLMResponse] = field(default_factory=list)

    def record(self, r: LLMResponse) -> None:
        self.calls.append(r)

    @property
    def total_cost_usd(self) -> float:
        return sum(c.estimated_cost_usd for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return sum(c.input_tokens + c.output_tokens for c in self.calls)

    @property
    def total_latency_ms(self) -> float:
        return sum(c.latency_ms for c in self.calls)

    def as_dict(self) -> dict:
        return {
            "call_count": len(self.calls),
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_latency_ms": round(self.total_latency_ms, 1),
            "degraded": any(c.degraded for c in self.calls),
        }


class ModelRouter:
    """Chooses a model per task. Planning gets the stronger model; narration and
    classification can use a cheaper one without measurable quality loss."""

    def select(self, task: TaskKind, *, complexity: float = 0.5) -> str:
        if task is TaskKind.PLANNING or complexity > 0.75:
            return settings.LLM_PLANNING_MODEL
        return settings.LLM_NARRATIVE_MODEL


class LLMGateway:
    def __init__(self, router: ModelRouter | None = None) -> None:
        self.router = router or ModelRouter()

    async def complete(
        self,
        *,
        task: TaskKind,
        system: str,
        prompt: str,
        max_tokens: int = 1200,
        complexity: float = 0.5,
        fallback: str = "",
    ) -> LLMResponse:
        model = self.router.select(task, complexity=complexity)
        started = time.perf_counter()

        if not settings.llm_enabled:
            log.info("llm.degraded task=%s reason=no_provider_configured", task)
            return LLMResponse(
                text=fallback, model="deterministic-fallback", provider="none",
                latency_ms=(time.perf_counter() - started) * 1000, degraded=True,
            )

        try:
            if settings.LLM_PROVIDER == "anthropic":
                text, in_tok, out_tok = await self._anthropic(model, system, prompt, max_tokens)
            else:
                text, in_tok, out_tok = await self._openai(model, system, prompt, max_tokens)
        except Exception:
            log.exception("llm.call_failed task=%s model=%s", task, model)
            return LLMResponse(
                text=fallback, model=model, provider=settings.LLM_PROVIDER,
                latency_ms=(time.perf_counter() - started) * 1000, degraded=True,
            )

        return LLMResponse(
            text=text, model=model, provider=settings.LLM_PROVIDER,
            input_tokens=in_tok, output_tokens=out_tok,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    async def complete_json(self, *, schema_hint: str, fallback: dict, **kwargs) -> dict:
        """Structured output. Never trusts the model to emit clean JSON -- strips
        fences, and falls back to the deterministic default on any parse failure."""
        kwargs["system"] = (
            kwargs.get("system", "")
            + "\n\nRespond with a single JSON object and nothing else. "
              "No markdown fences, no prose. Schema: " + schema_hint
        )
        kwargs.setdefault("fallback", json.dumps(fallback))
        resp = await self.complete(**kwargs)
        raw = resp.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1] if "```" in raw[3:] else raw[3:]
            raw = raw.removeprefix("json").strip()
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else fallback
        except json.JSONDecodeError:
            log.warning("llm.json_parse_failed; using deterministic fallback")
            return fallback

    # -- providers ------------------------------------------------------------

    async def _anthropic(self, model, system, prompt, max_tokens):
        import anthropic
        client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY, timeout=settings.LLM_TIMEOUT_SECONDS
        )
        msg = await client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text")
        return text, msg.usage.input_tokens, msg.usage.output_tokens

    async def _openai(self, model, system, prompt, max_tokens):
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY,
                             timeout=settings.LLM_TIMEOUT_SECONDS)
        resp = await client.chat.completions.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
        )
        u = resp.usage
        return resp.choices[0].message.content or "", u.prompt_tokens, u.completion_tokens
