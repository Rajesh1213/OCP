"""Abstract storage interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ocp_server.models import Chunk, StateEntry, Scope


class BaseStore(ABC):

    @abstractmethod
    async def setup(self) -> None: ...

    # --- workspace ---
    @abstractmethod
    async def workspace_exists(self, workspace_id: str) -> bool: ...

    @abstractmethod
    async def create_workspace(self, workspace_id: str, root_uri: str, name: str | None, metadata: dict) -> None: ...

    @abstractmethod
    async def get_workspace_root(self, workspace_id: str) -> str | None: ...

    @abstractmethod
    async def list_all_workspaces(self) -> list[dict]: ...

    # --- chunks ---
    @abstractmethod
    async def upsert_chunk(self, chunk: Chunk, embedding: list[float]) -> None: ...

    @abstractmethod
    async def get_chunk(self, chunk_id: str) -> Chunk | None: ...

    @abstractmethod
    async def invalidate_chunks_by_path(self, workspace_id: str, paths: list[str]) -> list[str]:
        """Mark chunks stale; returns list of invalidated chunk IDs (B4)."""
        ...

    @abstractmethod
    async def is_chunk_stale(self, chunk_id: str) -> bool: ...

    @abstractmethod
    async def search_chunks(
        self, workspace_id: str, query_embedding: list[float], k: int, filters: dict[str, Any] | None
    ) -> list[tuple[Chunk, float]]: ...

    @abstractmethod
    async def list_chunks(
        self, workspace_id: str, filters: dict | None, cursor: str | None
    ) -> tuple[list[Chunk], str | None]: ...

    # --- state ---
    @abstractmethod
    async def state_set(self, entry: StateEntry, if_version: int | None = None) -> int:
        """Write state entry. Raises ConflictError if if_version mismatches (B2)."""
        ...

    @abstractmethod
    async def state_get(self, key: str, scope: Scope, workspace_id: str | None, session_id: str | None, agent_id: str | None) -> StateEntry | None: ...

    @abstractmethod
    async def state_list(self, prefix: str | None, scope: Scope | None, workspace_id: str | None, session_id: str | None, agent_id: str | None, cursor: str | None) -> tuple[list[StateEntry], str | None]: ...

    @abstractmethod
    async def state_delete(self, key: str, scope: Scope, workspace_id: str | None, session_id: str | None, agent_id: str | None, if_version: int | None) -> bool:
        """Delete state. Raises ConflictError on if_version mismatch (S1)."""
        ...

    # --- sessions ---
    @abstractmethod
    async def session_open(self, workspace_id: str, session_id: str, ttl_seconds: int | None, metadata: dict) -> None: ...

    @abstractmethod
    async def session_close(self, session_id: str) -> bool: ...

    @abstractmethod
    async def session_exists(self, session_id: str) -> bool: ...

    @abstractmethod
    async def get_session_workspace(self, session_id: str) -> str | None:
        """Return workspace_id for a session (used by checkpoint/restore)."""
        ...

    # --- events ---
    @abstractmethod
    async def append_event(self, workspace_id: str, subscription_id: str, event_type: str, payload: dict) -> str: ...

    @abstractmethod
    async def list_events(self, subscription_id: str, since: str | None) -> list[dict]: ...

    @abstractmethod
    async def list_events_for_workspace(self, workspace_id: str, since: str) -> list[dict]:
        """Replay events for a workspace since a given event_id (S5)."""
        ...

    @abstractmethod
    async def create_subscription(self, workspace_id: str, types: list[str] | None, session_id: str | None) -> str: ...

    @abstractmethod
    async def delete_subscription(self, subscription_id: str) -> bool: ...

    @abstractmethod
    async def get_subscriptions_for_workspace(self, workspace_id: str) -> list[dict]: ...

    # --- TTL / maintenance ---
    @abstractmethod
    async def purge_expired_state(self) -> int: ...

    @abstractmethod
    async def purge_expired_state_entries(self) -> int: ...

    @abstractmethod
    async def purge_expired_sessions_with_ids(self) -> list[tuple[str, str]]: ...

    # --- checkpoint restore ---
    @abstractmethod
    async def copy_session_state(self, src_session_id: str, dst_session_id: str) -> int: ...

    @abstractmethod
    async def get_checkpoint(self, checkpoint_id: str) -> dict | None: ...

    # --- chunk helpers for hash-mismatch detection (§6.1 trigger 3) ---
    @abstractmethod
    async def get_active_chunk_ids_for_uri(self, workspace_id: str, uri: str) -> list[str]:
        """Return non-stale chunk IDs whose source_uri matches exactly."""
        ...

    @abstractmethod
    async def mark_chunks_stale(self, chunk_ids: list[str]) -> None:
        """Bulk-mark the given chunk IDs as stale."""
        ...

    # --- session GC (§4.4 MUST) ---
    @abstractmethod
    async def delete_session_state(self, session_id: str) -> int:
        """Delete all state entries scoped to session_id. Returns deleted count."""
        ...

    # --- prompt traces ---
    @abstractmethod
    async def save_prompt_trace(self, trace: dict) -> None: ...

    @abstractmethod
    async def record_prompt_result(self, trace_id: str, result: str) -> bool: ...

    @abstractmethod
    async def get_prompt_trace(self, trace_id: str) -> dict | None: ...

    @abstractmethod
    async def get_trace_stats(
        self, workspace_id: str | None = None, since: str | None = None
    ) -> dict: ...

    @abstractmethod
    async def export_traces(
        self,
        fmt: str = "alpaca",
        workspace_id: str | None = None,
        since: str | None = None,
        only_completed: bool = True,
    ) -> list[dict]: ...
