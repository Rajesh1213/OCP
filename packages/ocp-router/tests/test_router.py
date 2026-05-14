"""Tests for OCPRouter — all backends mocked, no network or API keys required."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from ocp_router.backends.base import (
    ClassifyResult,
    GenerateRequest,
    GenerateResponse,
    RouteResult,
)
from ocp_router.classifier import TaskClassifier
from ocp_router.factory import make_paid_backend, make_router
from ocp_router.router import OCPRouter


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _make_backend(model: str, text: str = "ok") -> MagicMock:
    """Return a mock ModelBackend that returns a fixed GenerateResponse."""
    backend = MagicMock()
    backend.model = model
    backend.is_available = AsyncMock(return_value=True)
    backend.generate = AsyncMock(return_value=GenerateResponse(
        text=text,
        model=model,
        prompt_tokens=10,
        completion_tokens=5,
        duration_ms=42.0,
    ))
    return backend


def _make_router(threshold: float = 0.5) -> tuple[OCPRouter, MagicMock, MagicMock]:
    local = _make_backend("llama3.2", text="local response")
    paid  = _make_backend("claude-sonnet-4-6", text="paid response")
    clf   = TaskClassifier(threshold=threshold)
    router = OCPRouter(local=local, paid=paid, classifier=clf)
    return router, local, paid


# ------------------------------------------------------------------ #
# RouteResult structure                                                #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_route_returns_route_result():
    router, _, _ = _make_router()
    result = await router.route("explain this function")
    assert isinstance(result, RouteResult)
    assert result.text
    assert result.route_to in {"local", "paid"}
    assert isinstance(result.classify, ClassifyResult)
    assert result.duration_ms >= 0


# ------------------------------------------------------------------ #
# Simple prompt → local backend                                        #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_simple_prompt_dispatches_to_local():
    router, local, paid = _make_router()
    result = await router.route("explain this function")
    assert result.route_to == "local"
    assert result.text == "local response"
    assert result.model == "llama3.2"
    local.generate.assert_called_once()
    paid.generate.assert_not_called()


@pytest.mark.asyncio
async def test_simple_prompt_classify_embedded_in_result():
    router, _, _ = _make_router()
    result = await router.route("what does add() do?")
    assert result.classify.route_to == "local"
    assert result.classify.complexity_score < 0.5


# ------------------------------------------------------------------ #
# Complex prompt → paid backend                                        #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_complex_prompt_dispatches_to_paid():
    router, local, paid = _make_router()
    result = await router.route(
        "review the security vulnerabilities across all endpoints in the API"
    )
    assert result.route_to == "paid"
    assert result.text == "paid response"
    assert result.model == "claude-sonnet-4-6"
    paid.generate.assert_called_once()
    local.generate.assert_not_called()


@pytest.mark.asyncio
async def test_complex_prompt_classify_embedded_in_result():
    router, _, _ = _make_router()
    result = await router.route(
        "design the architecture for a new microservice payment system"
    )
    assert result.classify.route_to == "paid"
    assert result.classify.complexity_score >= 0.5


# ------------------------------------------------------------------ #
# Request fields forwarded correctly                                   #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_system_prompt_forwarded():
    router, local, _ = _make_router()
    await router.route(
        "explain add()",
        system="You are a Python expert.",
        max_tokens=256,
        temperature=0.1,
    )
    req: GenerateRequest = local.generate.call_args[0][0]
    assert req.system == "You are a Python expert."
    assert req.max_tokens == 256
    assert req.temperature == 0.1


@pytest.mark.asyncio
async def test_messages_forwarded():
    router, local, _ = _make_router()
    msgs = [{"role": "user", "content": "hi"}]
    await router.route("", messages=msgs)
    req: GenerateRequest = local.generate.call_args[0][0]
    assert req.messages == msgs


# ------------------------------------------------------------------ #
# Token counts and duration in result                                  #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_token_counts_propagated():
    router, _, _ = _make_router()
    result = await router.route("explain this function")
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.duration_ms == 42.0


# ------------------------------------------------------------------ #
# Threshold controls routing                                           #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_low_threshold_routes_everything_to_paid():
    router, local, paid = _make_router(threshold=0.0)
    await router.route("explain this function")
    paid.generate.assert_called_once()
    local.generate.assert_not_called()


@pytest.mark.asyncio
async def test_high_threshold_routes_everything_to_local():
    router, local, paid = _make_router(threshold=1.0)
    await router.route(
        "review the security vulnerabilities across all endpoints in the API"
    )
    local.generate.assert_called_once()
    paid.generate.assert_not_called()


# ------------------------------------------------------------------ #
# Availability checks                                                  #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_local_available_delegates_to_backend():
    router, local, _ = _make_router()
    assert await router.local_available() is True
    local.is_available.assert_called_once()


@pytest.mark.asyncio
async def test_paid_available_delegates_to_backend():
    router, _, paid = _make_router()
    paid.is_available = AsyncMock(return_value=False)
    assert await router.paid_available() is False


# ------------------------------------------------------------------ #
# Factory — make_paid_backend                                          #
# ------------------------------------------------------------------ #

def test_make_paid_backend_anthropic(monkeypatch):
    monkeypatch.setenv("OCP_PAID_BACKEND", "anthropic")
    monkeypatch.setenv("OCP_PAID_MODEL", "claude-opus-4-7")
    from ocp_router.backends.anthropic import AnthropicBackend
    backend = make_paid_backend()
    assert isinstance(backend, AnthropicBackend)
    assert backend.model == "claude-opus-4-7"


def test_make_paid_backend_openai(monkeypatch):
    monkeypatch.setenv("OCP_PAID_BACKEND", "openai")
    monkeypatch.setenv("OCP_PAID_MODEL", "gpt-4o")
    from ocp_router.backends.openai import OpenAIBackend
    backend = make_paid_backend()
    assert isinstance(backend, OpenAIBackend)
    assert backend.model == "gpt-4o"


def test_make_paid_backend_unknown_raises(monkeypatch):
    monkeypatch.setenv("OCP_PAID_BACKEND", "grok")
    with pytest.raises(ValueError, match="OCP_PAID_BACKEND"):
        make_paid_backend()


def test_make_paid_backend_default_is_anthropic(monkeypatch):
    monkeypatch.delenv("OCP_PAID_BACKEND", raising=False)
    from ocp_router.backends.anthropic import AnthropicBackend
    backend = make_paid_backend()
    assert isinstance(backend, AnthropicBackend)
    assert backend.model == "claude-sonnet-4-6"


# ------------------------------------------------------------------ #
# Factory — make_router                                                #
# ------------------------------------------------------------------ #

def test_make_router_returns_ocp_router(monkeypatch):
    monkeypatch.setenv("OCP_PAID_BACKEND", "anthropic")
    router = make_router()
    assert isinstance(router, OCPRouter)
