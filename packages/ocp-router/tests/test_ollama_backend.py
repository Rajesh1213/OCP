"""Smoke tests for OllamaBackend.

Fast tests mock the HTTP layer (no Ollama required).
The integration test is skipped unless Ollama is actually running.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ocp_router.backends.base import GenerateRequest, LocalModelBackend
from ocp_router.backends.ollama import OllamaBackend
from ocp_router.factory import make_local_backend


# ------------------------------------------------------------------ #
# Protocol conformance                                                 #
# ------------------------------------------------------------------ #

def test_ollama_backend_satisfies_protocol():
    backend = OllamaBackend()
    assert isinstance(backend, LocalModelBackend)


def test_factory_returns_ollama_by_default(monkeypatch):
    monkeypatch.delenv("OCP_LOCAL_BACKEND", raising=False)
    backend = make_local_backend()
    assert isinstance(backend, OllamaBackend)


def test_factory_respects_env_vars(monkeypatch):
    monkeypatch.setenv("OCP_LOCAL_MODEL", "mistral")
    monkeypatch.setenv("OCP_OLLAMA_URL", "http://gpu-box:11434")
    monkeypatch.setenv("OCP_LOCAL_TIMEOUT", "120")
    backend = make_local_backend()
    assert isinstance(backend, OllamaBackend)
    assert backend.model == "mistral"
    assert backend._base_url == "http://gpu-box:11434"
    assert backend._timeout.total == 120.0


def test_factory_raises_on_unknown_backend(monkeypatch):
    monkeypatch.setenv("OCP_LOCAL_BACKEND", "gpt-zero")
    with pytest.raises(ValueError, match="OCP_LOCAL_BACKEND"):
        make_local_backend()


# ------------------------------------------------------------------ #
# is_available — mocked HTTP                                          #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_is_available_true_when_model_listed():
    backend = OllamaBackend(model="llama3.2")
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={
        "models": [{"name": "llama3.2:latest"}, {"name": "mistral:latest"}]
    })
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        assert await backend.is_available() is True


@pytest.mark.asyncio
async def test_is_available_false_when_model_missing():
    backend = OllamaBackend(model="llama3.2")
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"models": [{"name": "mistral:latest"}]})
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.get = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        assert await backend.is_available() is False


@pytest.mark.asyncio
async def test_is_available_false_on_connection_error():
    backend = OllamaBackend()
    with patch("aiohttp.ClientSession", side_effect=Exception("connection refused")):
        assert await backend.is_available() is False


# ------------------------------------------------------------------ #
# generate — mocked HTTP                                              #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_generate_text_prompt():
    backend = OllamaBackend(model="llama3.2")
    api_response = {
        "model": "llama3.2",
        "response": "Paris is the capital of France.",
        "prompt_eval_count": 10,
        "eval_count": 7,
    }

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=api_response)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        req = GenerateRequest(prompt="What is the capital of France?")
        resp = await backend.generate(req)

    assert resp.text == "Paris is the capital of France."
    assert resp.model == "llama3.2"
    assert resp.prompt_tokens == 10
    assert resp.completion_tokens == 7
    assert resp.duration_ms > 0

    # Must use /api/generate for plain prompts
    call_args = mock_session.post.call_args
    assert call_args[0][0].endswith("/api/generate")
    payload = call_args[1]["json"]
    assert payload["stream"] is False
    assert "prompt" in payload


@pytest.mark.asyncio
async def test_generate_chat_messages():
    backend = OllamaBackend(model="llama3.2")
    api_response = {
        "model": "llama3.2",
        "message": {"role": "assistant", "content": "Hello there!"},
        "prompt_eval_count": 5,
        "eval_count": 3,
    }

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = AsyncMock(return_value=api_response)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    mock_session = AsyncMock()
    mock_session.post = MagicMock(return_value=mock_resp)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        req = GenerateRequest(
            prompt="",
            messages=[{"role": "user", "content": "Hi"}],
            system="You are a helpful assistant.",
        )
        resp = await backend.generate(req)

    assert resp.text == "Hello there!"

    # Must use /api/chat for message lists
    call_args = mock_session.post.call_args
    assert call_args[0][0].endswith("/api/chat")
    payload = call_args[1]["json"]
    # System message should be prepended
    assert payload["messages"][0]["role"] == "system"


# ------------------------------------------------------------------ #
# Integration test — skipped unless Ollama is running                 #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
@pytest.mark.integration
async def test_ollama_live_generate():
    """Requires: ollama serve + ollama pull llama3.2"""
    backend = OllamaBackend(model="llama3.2")
    if not await backend.is_available():
        pytest.skip("Ollama not running or llama3.2 not pulled")

    req = GenerateRequest(
        prompt="Reply with exactly three words: the sky is",
        max_tokens=10,
        temperature=0.0,
    )
    resp = await backend.generate(req)
    assert len(resp.text) > 0
    assert resp.completion_tokens > 0
    assert resp.duration_ms > 0
