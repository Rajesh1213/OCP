"""Ollama local model backend.

Talks to a running Ollama instance via its HTTP API.
Env vars (all optional):
  OCP_OLLAMA_URL    — base URL          (default: http://localhost:11434)
  OCP_LOCAL_MODEL   — model to use      (default: llama3.2)
  OCP_LOCAL_TIMEOUT — request timeout s (default: 60)
"""
from __future__ import annotations

import time
from typing import Any

import aiohttp

from ocp_router.backends.base import GenerateRequest, GenerateResponse


class OllamaBackend:
    """Async client for a local Ollama instance."""

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    # ------------------------------------------------------------------ #
    # Protocol properties                                                  #
    # ------------------------------------------------------------------ #

    @property
    def model(self) -> str:
        return self._model

    # ------------------------------------------------------------------ #
    # Health check                                                         #
    # ------------------------------------------------------------------ #

    async def is_available(self) -> bool:
        """Return True if Ollama is running and the model is pulled."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(f"{self._base_url}/api/tags") as resp:
                    if resp.status != 200:
                        return False
                    data = await resp.json()
                    names = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
                    target = self._model.split(":")[0]
                    return target in names
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Inference                                                            #
    # ------------------------------------------------------------------ #

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Run inference. Uses /api/chat if messages are provided, else /api/generate."""
        t0 = time.monotonic()

        if request.messages:
            payload, endpoint = self._build_chat_payload(request), "/api/chat"
        else:
            payload, endpoint = self._build_generate_payload(request), "/api/generate"

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            async with session.post(f"{self._base_url}{endpoint}", json=payload) as resp:
                resp.raise_for_status()
                data: dict[str, Any] = await resp.json()

        duration_ms = (time.monotonic() - t0) * 1000

        if request.messages:
            text = data.get("message", {}).get("content", "")
        else:
            text = data.get("response", "")

        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)

        return GenerateResponse(
            text=text,
            model=data.get("model", self._model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
        )

    # ------------------------------------------------------------------ #
    # Payload builders                                                     #
    # ------------------------------------------------------------------ #

    def _build_generate_payload(self, req: GenerateRequest) -> dict:
        payload: dict[str, Any] = {
            "model": self._model,
            "prompt": req.prompt,
            "stream": False,
            "options": {
                "temperature": req.temperature,
                "num_predict": req.max_tokens,
            },
        }
        if req.system:
            payload["system"] = req.system
        return payload

    def _build_chat_payload(self, req: GenerateRequest) -> dict:
        messages = list(req.messages)
        if req.system and not any(m.get("role") == "system" for m in messages):
            messages = [{"role": "system", "content": req.system}] + messages
        return {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": req.temperature,
                "num_predict": req.max_tokens,
            },
        }
