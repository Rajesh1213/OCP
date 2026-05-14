"""OCP Router — hybrid local/cloud model routing layer."""
from ocp_router.backends.base import (
    ClassifyResult,
    GenerateRequest,
    GenerateResponse,
    LocalModelBackend,
    RouteTarget,
    TaskType,
)
from ocp_router.backends.ollama import OllamaBackend
from ocp_router.classifier import TaskClassifier
from ocp_router.factory import make_local_backend

__all__ = [
    # Backends
    "OllamaBackend",
    "LocalModelBackend",
    # Inference types
    "GenerateRequest",
    "GenerateResponse",
    # Classifier
    "TaskClassifier",
    "ClassifyResult",
    "TaskType",
    "RouteTarget",
    # Factory
    "make_local_backend",
]
