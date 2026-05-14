"""OpenAI paid backend.

Optional dependency — install with: pip install ocp-router[openai]

Env vars:
  OPENAI_API_KEY    — required
  OCP_PAID_MODEL    — model ID (default: gpt-4o-mini)
  OCP_PAID_MAX_TOKENS — max tokens for paid responses (default: 4096)
"""
from __future__ import annotations

import time

from ocp_router.backends.base import GenerateRequest, GenerateResponse


class OpenAIBackend:
    """OpenAI backend using the official OpenAI SDK."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        max_tokens: int = 4096,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._client = None

    @property
    def model(self) -> str:
        return self._model

    def _get_client(self):
        if self._client is None:
            try:
                import openai  # type: ignore[import-untyped]
            except ImportError:
                raise ImportError(
                    "openai package is required for OpenAIBackend. "
                    "Install with: pip install ocp-router[openai]"
                )
            self._client = openai.AsyncOpenAI()
        return self._client

    async def is_available(self) -> bool:
        """Return True if the API key is set and the SDK is installed."""
        import importlib.util
        import os
        return (
            importlib.util.find_spec("openai") is not None
            and bool(os.environ.get("OPENAI_API_KEY"))
        )

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        client = self._get_client()
        t0 = time.monotonic()

        messages = self._build_messages(request)
        response = await client.chat.completions.create(
            model=self._model,
            messages=messages,
            max_tokens=min(request.max_tokens, self._max_tokens),
            temperature=request.temperature,
        )

        duration_ms = (time.monotonic() - t0) * 1000
        choice = response.choices[0]
        text = choice.message.content or ""

        return GenerateResponse(
            text=text,
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            duration_ms=duration_ms,
        )

    def _build_messages(self, request: GenerateRequest) -> list[dict]:
        if request.messages:
            msgs = list(request.messages)
            if request.system and not any(m.get("role") == "system" for m in msgs):
                msgs = [{"role": "system", "content": request.system}] + msgs
            return msgs
        msgs = []
        if request.system:
            msgs.append({"role": "system", "content": request.system})
        msgs.append({"role": "user", "content": request.prompt})
        return msgs
