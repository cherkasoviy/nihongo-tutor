"""Which Anthropic client gets built, and how a refusal is retried on each route.

Direct Anthropic billing is unavailable, so Claude is reached through Vertex AI using the same
Google service account as TTS/STT. Vertex does not offer the server-side ``fallbacks`` parameter,
so the refusal retry has to move into the SDK's client-side middleware — a difference that is
invisible until a refusal actually happens in production.
"""

from __future__ import annotations

from anthropic import AsyncAnthropic, AsyncAnthropicVertex, BetaRefusalFallbackMiddleware
from pydantic import SecretStr

from app.ai.client import ClaudeClient, build_async_client
from app.config import Settings


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "jwt_secret": SecretStr("x" * 32),
        "anthropic_api_key": SecretStr("sk-ant-test"),
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def test_without_a_vertex_project_the_first_party_client_is_used() -> None:
    settings = _settings()
    assert settings.uses_vertex is False
    assert isinstance(build_async_client(settings), AsyncAnthropic)


def test_a_vertex_project_routes_through_vertex() -> None:
    settings = _settings(anthropic_vertex_project="nihongo-tutor", anthropic_vertex_region="europe-west1")
    assert settings.uses_vertex is True
    assert isinstance(build_async_client(settings), AsyncAnthropicVertex)


def test_vertex_carries_the_client_side_refusal_middleware() -> None:
    """The server-side parameter does not exist there, so without this a refusal is simply a dead end."""
    client = build_async_client(_settings(anthropic_vertex_project="nihongo-tutor"))
    assert any(isinstance(m, BetaRefusalFallbackMiddleware) for m in client.middleware)


def test_the_first_party_client_does_not_need_the_middleware() -> None:
    client = build_async_client(_settings())
    # Not vacuous: the attribute exists on both clients and is an empty tuple here.
    assert client.middleware == ()


def test_server_side_fallback_params_are_sent_only_off_vertex() -> None:
    """Sending ``fallbacks`` to Vertex would be an error, and omitting it first-party loses the retry."""
    assert ClaudeClient(_settings())._server_side_fallbacks is True
    assert ClaudeClient(_settings(anthropic_vertex_project="nihongo-tutor"))._server_side_fallbacks is False
