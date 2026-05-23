"""Tests for prompt.prepare and prompt.record_result — all I/O mocked."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ocp_server.tools.prompt import (
    TraceNotFoundError,
    _count_tokens,
    prompt_prepare,
    prompt_record_result,
)


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _make_store(*, workspace_exists: bool = True) -> MagicMock:
    store = MagicMock()
    store.workspace_exists = AsyncMock(return_value=workspace_exists)
    store.search_chunks = AsyncMock(return_value=[])
    store.state_list = AsyncMock(return_value=([], None))
    store.save_prompt_trace = AsyncMock()
    store.record_prompt_result = AsyncMock(return_value=True)
    store.get_prompt_trace = AsyncMock(return_value=None)
    return store


def _make_embedder() -> MagicMock:
    embedder = MagicMock()
    embedder.embed = AsyncMock(return_value=[0.1] * 16)
    return embedder


def _make_tokenizer() -> MagicMock:
    tokenizer = MagicMock()
    tokenizer.count = MagicMock(side_effect=lambda text: len(text.split()))
    return tokenizer


# ------------------------------------------------------------------ #
# _count_tokens                                                        #
# ------------------------------------------------------------------ #

def test_count_tokens_returns_int():
    assert isinstance(_count_tokens("hello world"), int)
    assert _count_tokens("hello world") > 0


def test_count_tokens_empty():
    assert _count_tokens("") == 0


# ------------------------------------------------------------------ #
# prompt_prepare — passthrough when no backend                        #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_prepare_passthrough_when_no_backend():
    store = _make_store()
    embedder = _make_embedder()
    tokenizer = _make_tokenizer()

    with patch("ocp_server.tools.prompt._local_backend", AsyncMock(return_value=None)):
        result = await prompt_prepare(
            store, embedder, tokenizer,
            prompt="Explain async/await in Python",
            workspace_id=None,
            session_id=None,
            target_model="default",
            budget_tokens=800,
        )

    assert result["optimized"] is False
    assert result["reason"] == "local_backend_unavailable"
    assert result["optimized_prompt"] == "Explain async/await in Python"
    assert result["compression_ratio"] == 1.0
    assert "trace_id" in result
    store.save_prompt_trace.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_passthrough_saves_trace():
    store = _make_store()
    embedder = _make_embedder()
    tokenizer = _make_tokenizer()

    with patch("ocp_server.tools.prompt._local_backend", AsyncMock(return_value=None)):
        await prompt_prepare(
            store, embedder, tokenizer,
            prompt="hello",
            workspace_id=None,
            session_id=None,
            target_model="claude",
            budget_tokens=800,
        )

    saved = store.save_prompt_trace.call_args[0][0]
    assert saved["raw_prompt"] == "hello"
    assert saved["optimized_prompt"] == "hello"
    assert saved["target_model"] == "claude"


# ------------------------------------------------------------------ #
# prompt_prepare — with backend                                        #
# ------------------------------------------------------------------ #

def _make_generate_response(text: str) -> MagicMock:
    from ocp_router.backends.base import GenerateResponse  # type: ignore[import]
    return GenerateResponse(
        text=text,
        model="llama3.2",
        prompt_tokens=20,
        completion_tokens=10,
        duration_ms=100.0,
    )


@pytest.mark.asyncio
async def test_prepare_with_backend_returns_optimized():
    store = _make_store()
    embedder = _make_embedder()
    tokenizer = _make_tokenizer()

    mock_backend = MagicMock()
    mock_backend.generate = AsyncMock(
        return_value=_make_generate_response("Explain async/await concisely.")
    )

    with patch("ocp_server.tools.prompt._local_backend", AsyncMock(return_value=mock_backend)):
        result = await prompt_prepare(
            store, embedder, tokenizer,
            prompt="Can you please explain to me in great detail how async/await works in Python?",
            workspace_id=None,
            session_id=None,
            target_model="claude",
            budget_tokens=800,
        )

    assert result["optimized"] is True
    assert result["optimized_prompt"] == "Explain async/await concisely."
    assert result["compression_ratio"] > 1.0
    assert "trace_id" in result
    store.save_prompt_trace.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_with_workspace_fetches_context():
    store = _make_store(workspace_exists=True)
    embedder = _make_embedder()
    tokenizer = _make_tokenizer()

    mock_backend = MagicMock()
    mock_backend.generate = AsyncMock(
        return_value=_make_generate_response("Short optimized prompt.")
    )

    with patch("ocp_server.tools.prompt._local_backend", AsyncMock(return_value=mock_backend)):
        await prompt_prepare(
            store, embedder, tokenizer,
            prompt="refactor auth",
            workspace_id="ws-1",
            session_id=None,
            target_model="default",
            budget_tokens=800,
        )

    store.search_chunks.assert_awaited_once()
    embedder.embed.assert_awaited_once_with("refactor auth")


@pytest.mark.asyncio
async def test_prepare_skips_context_when_workspace_missing():
    store = _make_store(workspace_exists=False)
    embedder = _make_embedder()
    tokenizer = _make_tokenizer()

    with patch("ocp_server.tools.prompt._local_backend", AsyncMock(return_value=None)):
        await prompt_prepare(
            store, embedder, tokenizer,
            prompt="hello",
            workspace_id="ws-missing",
            session_id=None,
            target_model="default",
            budget_tokens=800,
        )

    store.search_chunks.assert_not_awaited()


# ------------------------------------------------------------------ #
# prompt_record_result                                                 #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_record_result_success():
    store = _make_store()
    result = await prompt_record_result(store, "trace-123", "The answer is 42.")
    assert result["recorded"] is True
    assert result["trace_id"] == "trace-123"
    store.record_prompt_result.assert_awaited_once_with("trace-123", "The answer is 42.")


@pytest.mark.asyncio
async def test_record_result_raises_when_trace_missing():
    store = _make_store()
    store.record_prompt_result = AsyncMock(return_value=False)

    with pytest.raises(TraceNotFoundError):
        await prompt_record_result(store, "no-such-trace", "result")
