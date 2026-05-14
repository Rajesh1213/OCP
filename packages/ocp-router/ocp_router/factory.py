"""Factories for OCP router backends and the router itself.

All config is read from env vars — no code changes needed for different setups.

Local backend env vars:
  OCP_LOCAL_BACKEND  — "ollama" (default)
  OCP_LOCAL_MODEL    — model identifier (default: llama3.2)
  OCP_OLLAMA_URL     — Ollama base URL (default: http://localhost:11434)
  OCP_LOCAL_TIMEOUT  — inference timeout seconds (default: 60)

Paid backend env vars:
  OCP_PAID_BACKEND     — "anthropic" (default) | "openai"
  OCP_PAID_MODEL       — model identifier (default: claude-sonnet-4-6 / gpt-4o-mini)
  OCP_PAID_MAX_TOKENS  — max tokens for paid responses (default: 4096)
  ANTHROPIC_API_KEY    — required for anthropic backend
  OPENAI_API_KEY       — required for openai backend

Router env vars:
  OCP_ROUTE_THRESHOLD  — complexity score threshold for paid routing (default: 0.5)
"""
from __future__ import annotations

import os

from ocp_router.backends.base import LocalModelBackend, ModelBackend
from ocp_router.backends.ollama import OllamaBackend
from ocp_router.classifier import TaskClassifier
from ocp_router.router import OCPRouter


def make_local_backend() -> LocalModelBackend:
    backend = os.environ.get("OCP_LOCAL_BACKEND", "ollama").lower()

    if backend == "ollama":
        return OllamaBackend(
            model=os.environ.get("OCP_LOCAL_MODEL", "llama3.2"),
            base_url=os.environ.get("OCP_OLLAMA_URL", "http://localhost:11434"),
            timeout=float(os.environ.get("OCP_LOCAL_TIMEOUT", "60")),
        )

    raise ValueError(
        f"Unknown OCP_LOCAL_BACKEND={backend!r}. Supported: ollama"
    )


def make_paid_backend() -> ModelBackend:
    backend = os.environ.get("OCP_PAID_BACKEND", "anthropic").lower()
    model_default = "claude-sonnet-4-6" if backend == "anthropic" else "gpt-4o-mini"
    model = os.environ.get("OCP_PAID_MODEL", model_default)
    max_tokens = int(os.environ.get("OCP_PAID_MAX_TOKENS", "4096"))

    if backend == "anthropic":
        from ocp_router.backends.anthropic import AnthropicBackend
        return AnthropicBackend(model=model, max_tokens=max_tokens)

    if backend == "openai":
        from ocp_router.backends.openai import OpenAIBackend
        return OpenAIBackend(model=model, max_tokens=max_tokens)

    raise ValueError(
        f"Unknown OCP_PAID_BACKEND={backend!r}. Supported: anthropic, openai"
    )


def make_router() -> OCPRouter:
    """Build a fully configured OCPRouter from env vars."""
    return OCPRouter(
        local=make_local_backend(),
        paid=make_paid_backend(),
        classifier=TaskClassifier(),
    )
