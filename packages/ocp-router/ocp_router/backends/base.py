"""Protocol and type definitions for OCP router backends."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable


# ------------------------------------------------------------------ #
# Classifier types                                                     #
# ------------------------------------------------------------------ #

TaskType = Literal["retrieval", "summarise", "explain", "refactor", "debug", "architect", "unknown"]
RouteTarget = Literal["local", "paid"]


@dataclass
class ClassifyResult:
    complexity_score: float   # 0.0 = trivially simple, 1.0 = maximum complexity
    task_type: TaskType       # best-fit category for the request
    signals: list[str]        # heuristics that fired, for tracing / debugging
    route_to: RouteTarget     # derived decision: "local" or "paid"


# ------------------------------------------------------------------ #
# Inference types                                                      #
# ------------------------------------------------------------------ #

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
    """Minimum interface every model backend must implement (local or paid)."""

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


# Unified type used by OCPRouter — local and paid backends share the same interface.
ModelBackend = LocalModelBackend


# ------------------------------------------------------------------ #
# Router result                                                        #
# ------------------------------------------------------------------ #

@dataclass
class RouteResult:
    text: str
    route_to: RouteTarget        # which tier actually handled the request
    classify: ClassifyResult     # full classification with signals + score
    model: str                   # exact model identifier used
    prompt_tokens: int
    completion_tokens: int
    duration_ms: float
