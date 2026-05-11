"""§4.3 — State tools.

Fixes:
  B2  — if_version passed explicitly to store.state_set (not encoded in version field)
  S1  — state_delete raises CONFLICT on if_version mismatch
"""
from __future__ import annotations

from ocp_server.models import Scope, StateEntry
from ocp_server.storage.base import BaseStore
from ocp_server.storage.sqlite import ConflictError


async def state_set(
    store: BaseStore,
    key: str,
    value: object,
    scope: str,
    workspace_id: str | None,
    session_id: str | None,
    agent_id: str | None,
    ttl_seconds: int | None,
    if_version: int | None,
) -> dict:
    scope_enum = _parse_scope(scope, workspace_id, session_id, agent_id)
    entry = StateEntry(
        key=key,
        value=value,
        scope=scope_enum,
        workspace_id=workspace_id,
        session_id=session_id,
        agent_id=agent_id,
        ttl_seconds=ttl_seconds,
    )
    try:
        # B2: pass if_version separately — not encoded in entry.version
        version = await store.state_set(entry, if_version=if_version)
    except ConflictError as exc:
        raise StateConflictError(str(exc)) from exc
    return {"version": version}


async def state_get(
    store: BaseStore,
    key: str,
    scope: str | None,
    workspace_id: str | None,
    session_id: str | None,
    agent_id: str | None,
) -> dict:
    if scope:
        scope_enum = Scope(scope)
        entry = await store.state_get(key, scope_enum, workspace_id, session_id, agent_id)
    else:
        # §5.1 — resolve agent → session → global
        entry = None
        for s in (Scope.agent, Scope.session, Scope.global_):
            entry = await store.state_get(key, s, workspace_id, session_id, agent_id)
            if entry is not None:
                break
    return {"entry": entry.model_dump() if entry else None}


async def state_list(
    store: BaseStore,
    prefix: str | None,
    scope: str | None,
    workspace_id: str | None,
    session_id: str | None,
    agent_id: str | None,
    cursor: str | None,
) -> dict:
    scope_enum = Scope(scope) if scope else None
    entries, next_cursor = await store.state_list(
        prefix, scope_enum, workspace_id, session_id, agent_id, cursor
    )
    return {"entries": [e.model_dump() for e in entries], "next_cursor": next_cursor}


async def state_delete(
    store: BaseStore,
    key: str,
    scope: str,
    workspace_id: str | None,
    session_id: str | None,
    agent_id: str | None,
    if_version: int | None,
) -> dict:
    scope_enum = _parse_scope(scope, workspace_id, session_id, agent_id)
    try:
        # S1: ConflictError is now raised by the store on if_version mismatch
        deleted = await store.state_delete(
            key, scope_enum, workspace_id, session_id, agent_id, if_version
        )
    except ConflictError as exc:
        raise StateConflictError(str(exc)) from exc
    return {"deleted": deleted}


def _parse_scope(
    scope: str,
    workspace_id: str | None,
    session_id: str | None,
    agent_id: str | None,
) -> Scope:
    try:
        s = Scope(scope)
    except ValueError:
        raise ScopeInvalidError(f"Unknown scope: {scope}")
    if s == Scope.agent and not agent_id:
        raise ScopeInvalidError("agent_id required for agent scope")
    if s == Scope.session and not session_id:
        raise ScopeInvalidError("session_id required for session scope")
    if s in (Scope.session, Scope.global_) and not workspace_id:
        raise ScopeInvalidError("workspace_id required for session/global scope")
    return s


class StateConflictError(Exception):
    code = "CONFLICT"


class ScopeInvalidError(Exception):
    code = "SCOPE_INVALID"
