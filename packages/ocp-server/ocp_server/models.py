"""OCP core data model — §3."""
from __future__ import annotations

import base64
import hashlib
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Scope(str, Enum):
    agent = "agent"
    session = "session"
    global_ = "global"

    @classmethod
    def _missing_(cls, value: object) -> "Scope | None":
        if value == "global":
            return cls.global_
        return None


class ConformanceLevel(str, Enum):
    core = "core"
    core_coordination = "core+coordination"
    full = "full"


class SourceRange(BaseModel):
    start_byte: int | None = None
    end_byte: int | None = None
    start_line: int | None = None
    end_line: int | None = None


class ChunkSource(BaseModel):
    uri: str
    range: SourceRange | None = None
    content_hash: str


class Chunk(BaseModel):
    id: str
    workspace_id: str
    source: ChunkSource
    kind: str
    language: str | None = None
    symbol: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = 1


class StateEntry(BaseModel):
    key: str
    value: Any
    scope: Scope
    workspace_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    ttl_seconds: int | None = None
    updated_at: str | None = None
    version: int = 1

    @model_validator(mode="after")
    def _check_scope_ids(self) -> "StateEntry":
        if self.scope == Scope.agent and not self.agent_id:
            raise ValueError("agent_id required for agent scope")
        if self.scope == Scope.session and not self.session_id:
            raise ValueError("session_id required for session scope")
        if self.scope in (Scope.session, Scope.global_) and not self.workspace_id:
            raise ValueError("workspace_id required for session/global scope")
        return self


class EventEnvelope(BaseModel):
    type: str
    event_id: str
    timestamp: str
    workspace_id: str
    subscription_id: str
    payload: dict[str, Any] = Field(default_factory=dict)


# §3.4 — deterministic chunk ID
def make_chunk_id(workspace_id: str, uri: str, range_repr: str, content_hash: str) -> str:
    raw = f"{workspace_id}\x00{uri}\x00{range_repr}\x00{content_hash}".encode()
    digest = hashlib.sha256(raw).digest()
    return base64.b32encode(digest).decode().lower()[:24]


# §3.3 — deterministic workspace ID
def make_workspace_id(root_uri: str) -> str:
    digest = hashlib.sha256(root_uri.encode()).hexdigest()
    return f"ws_{digest[:16]}"
