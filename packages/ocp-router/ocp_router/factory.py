"""Factory for local model backends.

Reads env vars so callers don't need to pass config explicitly:
  OCP_LOCAL_BACKEND  — backend type: "ollama" (default, only option for now)
  OCP_LOCAL_MODEL    — model identifier passed to the backend
  OCP_OLLAMA_URL     — Ollama base URL
  OCP_LOCAL_TIMEOUT  — inference timeout in seconds
"""
from __future__ import annotations

import os

from ocp_router.backends.base import LocalModelBackend
from ocp_router.backends.ollama import OllamaBackend


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
