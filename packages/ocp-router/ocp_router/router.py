"""OCPRouter — wires classifier + local + paid backends into a single dispatch call.

Env vars (all optional — used by make_router()):
  OCP_ROUTE_THRESHOLD  — complexity score threshold for paid routing (default: 0.5)
  OCP_LOCAL_BACKEND    — local backend type: "ollama" (default)
  OCP_PAID_BACKEND     — paid backend type: "anthropic" | "openai"
"""
from __future__ import annotations

from ocp_router.backends.base import (
    GenerateRequest,
    ModelBackend,
    RouteResult,
)
from ocp_router.classifier import TaskClassifier


class OCPRouter:
    """Routes a prompt to the appropriate model tier based on task complexity.

    Simple tasks  → local backend (Ollama)
    Complex tasks → paid backend  (Anthropic / OpenAI)
    """

    def __init__(
        self,
        local: ModelBackend,
        paid: ModelBackend,
        classifier: TaskClassifier,
    ) -> None:
        self._local = local
        self._paid = paid
        self._classifier = classifier

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    async def route(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.2,
        messages: list[dict] | None = None,
    ) -> RouteResult:
        """Classify *prompt* and dispatch to the appropriate backend."""
        classify = self._classifier.classify(prompt)

        backend = self._paid if classify.route_to == "paid" else self._local
        request = GenerateRequest(
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages or [],
        )
        response = await backend.generate(request)

        return RouteResult(
            text=response.text,
            route_to=classify.route_to,
            classify=classify,
            model=response.model,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            duration_ms=response.duration_ms,
        )

    async def local_available(self) -> bool:
        """Return True if the local backend is reachable."""
        return await self._local.is_available()

    async def paid_available(self) -> bool:
        """Return True if the paid backend is reachable."""
        return await self._paid.is_available()
