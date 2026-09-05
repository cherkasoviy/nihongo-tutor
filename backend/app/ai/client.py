"""The single entry point for Claude API calls.

Phase 0 ships the wrapper only; tasks, prompts, budget reservation and the ledger arrive with
Phase 2/3. Rules baked in here (see docs/PLAN.md, "SDK rules"):

* ``AsyncAnthropic`` from ``anthropic`` 1.x; model ids come from ``Settings`` (never hard-coded).
* Structured output via ``client.beta.messages.parse(..., output_format=PydanticModel)`` which
  fills ``output_config.format`` with a strict JSON schema (``additionalProperties: false``).
* No assistant prefill, no ``temperature``, no ``budget_tokens``; effort via ``output_config.effort``.
* Server-side fallbacks on by default: ``betas=[SERVER_SIDE_FALLBACK_BETA]``, ``fallbacks="default"``.
* Always inspect ``stop_reason``: ``"refusal"`` degrades to a canned Russian message, never an error.
* Every response's ``usage`` is handed to a ``UsageSink`` so the ledger sees cache hits.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from anthropic import AsyncAnthropic
from anthropic.types.beta import BetaTextBlockParam
from pydantic import BaseModel

from app.bot import texts_ru
from app.config import Settings

SERVER_SIDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS_RUNTIME = 4096
MAX_TOKENS_BATCH = 16000
CACHE_TTL_1H: dict[str, str] = {"type": "ephemeral", "ttl": "1h"}

T = TypeVar("T", bound=BaseModel)


class Effort(enum.StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class Tier(enum.StrEnum):
    """Which configured model to use. ``fast`` is for chat turns, ``strong`` for generation."""

    fast = "fast"
    strong = "strong"


@dataclass(frozen=True, slots=True)
class Usage:
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    request_id: str | None
    stop_reason: str | None


@dataclass(frozen=True, slots=True)
class AIResult[T: BaseModel]:
    """Either ``value`` is set, or ``refused``/``degraded`` explains why and ``message_ru`` is safe to show."""

    value: T | None
    usage: Usage | None
    refused: bool = False
    degraded: bool = False
    message_ru: str | None = None
    raw_text: str | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None


class UsageSink(Protocol):
    async def record(self, *, task: str, user_id: Any | None, usage: Usage) -> None: ...


class NullUsageSink:
    async def record(self, *, task: str, user_id: Any | None, usage: Usage) -> None:
        return None


@dataclass(slots=True)
class SystemBlocks:
    """System prompt ordered stable -> volatile so prompt caching hits within a session."""

    stable: str
    learner_context: str | None = None
    extra: list[str] = field(default_factory=list)

    def to_params(self) -> list[BetaTextBlockParam]:
        blocks: list[BetaTextBlockParam] = [{"type": "text", "text": self.stable, "cache_control": CACHE_TTL_1H}]  # type: ignore[typeddict-item]
        if self.learner_context:
            blocks.append({"type": "text", "text": self.learner_context, "cache_control": CACHE_TTL_1H})  # type: ignore[typeddict-item]
        blocks.extend({"type": "text", "text": t} for t in self.extra)
        return blocks


class ClaudeClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncAnthropic | None = None,
        usage_sink: UsageSink | None = None,
    ) -> None:
        self._settings = settings
        api_key = settings.anthropic_api_key.get_secret_value() or None
        self._client = client or AsyncAnthropic(api_key=api_key)
        self._usage_sink: UsageSink = usage_sink or NullUsageSink()

    def model_for(self, tier: Tier) -> str:
        return self._settings.model_fast if tier is Tier.fast else self._settings.model_strong

    async def structured(
        self,
        *,
        task: str,
        system: SystemBlocks,
        messages: Sequence[dict[str, Any]],
        output_model: type[T],
        tier: Tier = Tier.fast,
        effort: Effort | None = Effort.low,
        max_tokens: int = MAX_TOKENS_RUNTIME,
        user_id: Any | None = None,
    ) -> AIResult[T]:
        """One structured-output turn. Never raises on refusal; API errors propagate to the caller,
        who degrades the step (the budget/degradation layer lands in Phase 3)."""
        model = self.model_for(tier)
        output_config: dict[str, Any] = {}
        if effort is not None:
            output_config["effort"] = effort.value

        response = await self._client.beta.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system.to_params(),
            messages=list(messages),  # type: ignore[arg-type]
            output_format=output_model,
            output_config=output_config or None,  # type: ignore[arg-type]
            betas=[SERVER_SIDE_FALLBACK_BETA],
            fallbacks="default",
        )

        usage = Usage(
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=response.usage.cache_read_input_tokens or 0,
            cache_write_tokens=response.usage.cache_creation_input_tokens or 0,
            request_id=getattr(response, "_request_id", None),
            stop_reason=response.stop_reason,
        )
        await self._usage_sink.record(task=task, user_id=user_id, usage=usage)

        if response.stop_reason == "refusal":
            return AIResult(value=None, usage=usage, refused=True, message_ru=texts_ru.AI_REFUSED)

        parsed = response.parsed_output
        if parsed is None:
            text = "".join(block.text for block in response.content if block.type == "text")
            return AIResult(value=None, usage=usage, degraded=True, message_ru=texts_ru.AI_UNAVAILABLE, raw_text=text)
        return AIResult(value=parsed, usage=usage)
