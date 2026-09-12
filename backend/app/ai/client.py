"""The single entry point for Claude API calls.

Phase 0 ships the wrapper only; tasks, prompts, budget reservation and the ledger arrive with
Phase 2/3. Rules baked in here (see docs/PLAN.md, "SDK rules"):

* ``AsyncAnthropic`` from ``anthropic`` 1.x; model ids come from ``Settings`` (never hard-coded).
* Structured output via ``client.beta.messages.parse(..., output_format=PydanticModel)`` which
  fills ``output_config.format`` with a strict JSON schema (``additionalProperties: false``).
* No assistant prefill, no ``temperature``, no ``budget_tokens``; effort via ``output_config.effort``.
* Refusals always fall back. On the first-party API that is server-side
  (``betas=[SERVER_SIDE_FALLBACK_BETA]``, ``fallbacks="default"``); on Vertex, where the parameter
  does not exist, it is the SDK's client-side ``BetaRefusalFallbackMiddleware``.
* Always inspect ``stop_reason``: ``"refusal"`` degrades to a canned Russian message, never an error.
* Every response's ``usage`` is handed to a ``UsageSink`` so the ledger sees cache hits.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from anthropic import AsyncAnthropic, AsyncAnthropicVertex, BetaRefusalFallbackMiddleware
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


AnyAsyncClient = AsyncAnthropic | AsyncAnthropicVertex


def build_async_client(settings: Settings) -> AnyAsyncClient:
    """Vertex when a project is configured, the first-party API otherwise.

    On Vertex the refusal retry has to happen client-side: the server-side ``fallbacks`` parameter
    is a first-party feature, so the SDK ships ``BetaRefusalFallbackMiddleware`` to splice the retry
    onto the same call. It takes an explicit model list — there is no ``"default"`` routing policy
    to defer to — which is why the fallback model is a setting.
    """
    if settings.uses_vertex:
        return AsyncAnthropicVertex(
            project_id=settings.anthropic_vertex_project,
            region=settings.anthropic_vertex_region,
            middleware=[BetaRefusalFallbackMiddleware([{"model": settings.anthropic_fallback_model}])],
        )
    return AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value() or None)


class ClaudeClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: AnyAsyncClient | None = None,
        usage_sink: UsageSink | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or build_async_client(settings)
        # Server-side fallbacks and the Batches API are first-party only; on Vertex the middleware
        # above covers refusals, and offline generation pays full price instead of the batch rate.
        self._server_side_fallbacks = not settings.uses_vertex
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

        fallback_params: dict[str, Any] = (
            {"betas": [SERVER_SIDE_FALLBACK_BETA], "fallbacks": "default"} if self._server_side_fallbacks else {}
        )
        # The two client classes are unrelated by inheritance — Vertex ships its own
        # ``beta.messages`` — so mypy cannot resolve one ``parse`` across the union even though the
        # signatures match. Narrowed here rather than weakening the attribute's type.
        response = await self._client.beta.messages.parse(  # type: ignore[misc]
            model=model,
            max_tokens=max_tokens,
            system=system.to_params(),
            messages=list(messages),  # type: ignore[arg-type]
            output_format=output_model,
            output_config=output_config or None,  # type: ignore[arg-type]
            **fallback_params,
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
