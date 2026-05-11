"""Typed wrappers for OCP responses."""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel


class WorkspaceRegistered(BaseModel):
    workspace_id: str
    created: bool


class IndexResult(BaseModel):
    indexed: int
    skipped: int
    duration_ms: int


class InvalidateResult(BaseModel):
    invalidated: int


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
    metadata: dict[str, Any] = {}
    version: int = 1


class SearchResult(BaseModel):
    chunks: list[Chunk]
    scores: list[float]


class PackResult(BaseModel):
    context: str
    chunks_used: list[str]
    state_used: list[str]
    tokens: int


class StateEntry(BaseModel):
    key: str
    value: Any
    scope: str
    workspace_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    ttl_seconds: int | None = None
    updated_at: str | None = None
    version: int = 1


class SetResult(BaseModel):
    version: int


class SessionResult(BaseModel):
    session_id: str


class HandoffResult(BaseModel):
    delivered: bool
    handoff_id: str


class CheckpointResult(BaseModel):
    checkpoint_id: str


class SubscriptionResult(BaseModel):
    subscription_id: str


class OCPError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")
