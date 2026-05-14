"""Protocol definition for local model backends."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class GenerateRequest:
    prompt: str
    system: str = ""
    max_tokens: int = 1024
    temperature: float = 0.2
    # Structured chat messages override prompt when provided
    messages: list[dict] = field(default_factory=list)


@dataclass
class GenerateResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    duration_ms: float


@runtime_checkable
class LocalModelBackend(Protocol):
    """Minimum interface every local model backend must implement."""

    @property
    def model(self) -> str:
        """Identifier of the loaded model."""
        ...

    async def is_available(self) -> bool:
        """Return True if the backend is reachable and ready."""
        ...

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        """Run inference and return the response."""
        ...
