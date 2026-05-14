"""OCP Router — hybrid local/cloud model routing layer."""
from ocp_router.backends.ollama import OllamaBackend
from ocp_router.backends.base import LocalModelBackend, GenerateRequest, GenerateResponse
from ocp_router.factory import make_local_backend

__all__ = [
    "OllamaBackend",
    "LocalModelBackend",
    "GenerateRequest",
    "GenerateResponse",
    "make_local_backend",
]
