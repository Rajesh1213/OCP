"""OCP Router — hybrid local/cloud model routing layer."""
from ocp_router.backends.base import (
    ClassifyResult,
    GenerateRequest,
    GenerateResponse,
    LocalModelBackend,
    ModelBackend,
    RouteResult,
    RouteTarget,
    TaskType,
)
from ocp_router.backends.ollama import OllamaBackend
from ocp_router.classifier import TaskClassifier
from ocp_router.factory import make_local_backend, make_paid_backend, make_router
from ocp_router.router import OCPRouter

__all__ = [
    # Router
    "OCPRouter",
    "RouteResult",
    # Backends
    "OllamaBackend",
    "LocalModelBackend",
    "ModelBackend",
    # Inference types
    "GenerateRequest",
    "GenerateResponse",
    # Classifier
    "TaskClassifier",
    "ClassifyResult",
    "TaskType",
    "RouteTarget",
    # Factories
    "make_local_backend",
    "make_paid_backend",
    "make_router",
]
