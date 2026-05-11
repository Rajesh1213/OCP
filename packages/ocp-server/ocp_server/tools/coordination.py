"""§4.4 — Coordination tools.

Fixes:
  B1  — session_checkpoint gets workspace_id from store; no longer crashes
  B5  — session_restore validates source session and propagates workspace_id properly
"""
from __future__ import annotations

import uuid

from ocp_server.models import Scope, StateEntry
from ocp_server.storage.base import BaseStore


async def session_open(
    store: BaseStore,
    workspace_id: str,
    session_id: str | None,
    ttl_seconds: int | None,
    metadata: dict,
) -> dict:
    sid = session_id or str(uuid.uuid4())
    await store.session_open(workspace_id, sid, ttl_seconds, metadata or {})
    return {"session_id": sid}


async def session_close(store: BaseStore, session_id: str) -> dict:
    closed = await store.session_close(session_id)
    return {"closed": closed}


async def session_handoff(
    store: BaseStore,
    session_id: str,
    from_agent: str,
    to_agent: str,
    message: object,
) -> dict:
    if not await store.session_exists(session_id):
        raise SessionNotFoundError(session_id)

    handoff_id = f"ho_{uuid.uuid4().hex[:12]}"

    # S2: _inbox is agent-scoped; workspace_id is intentionally None for agent scope.
    # session_id is stored for informational purposes only — not required by the model.
    entry = StateEntry(
        key="_inbox",
        value={"from_agent": from_agent, "handoff_id": handoff_id, "message": message},
        scope=Scope.agent,
        agent_id=to_agent,
        # workspace_id and session_id deliberately omitted: agent scope doesn't require them.
    )
    await store.state_set(entry)
    return {"delivered": True, "handoff_id": handoff_id}


async def session_checkpoint(
    store: BaseStore,
    session_id: str,
    label: str,
    include_state: bool,
) -> dict:
    if not await store.session_exists(session_id):
        raise SessionNotFoundError(session_id)

    # B1: look up the real workspace_id rather than hardcoding None
    workspace_id = await store.get_session_workspace(session_id)
    if workspace_id is None:
        raise SessionNotFoundError(session_id)

    checkpoint_id = f"ckpt_{uuid.uuid4().hex[:16]}"

    entry = StateEntry(
        key=f"_checkpoint.{checkpoint_id}",
        value={"label": label, "session_id": session_id},
        scope=Scope.session,
        session_id=session_id,
        workspace_id=workspace_id,   # ← B1 fix: validator satisfied
    )
    await store.state_set(entry)
    return {"checkpoint_id": checkpoint_id}


async def session_restore(store: BaseStore, checkpoint_id: str) -> dict:
    # B5: validate source session; propagate workspace_id cleanly
    ckpt = await store.get_checkpoint(checkpoint_id)
    if ckpt is None:
        raise SessionNotFoundError(f"checkpoint:{checkpoint_id}")

    src_session_id = ckpt["session_id"]

    # Get workspace from the sessions table (survives session.close)
    workspace_id = await store.get_session_workspace(src_session_id)
    if workspace_id is None:
        # Fall back to workspace embedded in checkpoint value
        workspace_id = ckpt.get("workspace_id", "")
    if not workspace_id:
        raise SessionNotFoundError(
            f"Cannot determine workspace for checkpoint {checkpoint_id}"
        )

    new_session_id = str(uuid.uuid4())
    await store.session_open(workspace_id, new_session_id, None, {})
    await store.copy_session_state(src_session_id, new_session_id)
    return {"session_id": new_session_id}


class SessionNotFoundError(Exception):
    code = "SESSION_NOT_FOUND"

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session not found: {session_id}")
