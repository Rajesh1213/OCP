"""Anthropic Claude paid backend.

Optional dependency — install with: pip install ocp-router[anthropic]

Env vars:
  ANTHROPIC_API_KEY   — required
  OCP_PAID_MODEL      — model ID (default: claude-sonnet-4-6)
  OCP_PAID_MAX_TOKENS — max tokens for paid responses (default: 4096)
"""
from __future__ import annotations

import time

from ocp_router.backends.base import GenerateRequest, GenerateResponse


class AnthropicBackend:
    """Anthropic Claude backend using the official Anthropic SDK."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
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
                import anthropic  # type: ignore[import-untyped]
            except ImportError:
                raise ImportError(
                    "anthropic package is required for AnthropicBackend. "
                    "Install with: pip install ocp-router[anthropic]"
                )
            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def is_available(self) -> bool:
        """Return True if the API key is set and the SDK is installed."""
        import importlib.util
        import os
        return (
            importlib.util.find_spec("anthropic") is not None
            and bool(os.environ.get("ANTHROPIC_API_KEY"))
        )

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        client = self._get_client()
        t0 = time.monotonic()

        messages = self._build_messages(request)
        kwargs = dict(
            model=self._model,
            max_tokens=min(request.max_tokens, self._max_tokens),
            messages=messages,
        )
        if request.system:
            kwargs["system"] = request.system

        response = await client.messages.create(**kwargs)

        duration_ms = (time.monotonic() - t0) * 1000
        text = response.content[0].text if response.content else ""

        return GenerateResponse(
            text=text,
            model=response.model,
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            duration_ms=duration_ms,
        )

    def _build_messages(self, request: GenerateRequest) -> list[dict]:
        if request.messages:
            return [m for m in request.messages if m.get("role") != "system"]
        return [{"role": "user", "content": request.prompt}]
