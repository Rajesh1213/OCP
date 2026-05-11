"""§4.4 — Coordination tools."""
from __future__ import annotations

import uuid

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

    # Deliver via _inbox state key for to_agent
    from ocp_server.models import Scope, StateEntry
    entry = StateEntry(
        key="_inbox",
        value={"from_agent": from_agent, "handoff_id": handoff_id, "message": message},
        scope=Scope.agent,
        workspace_id=None,
        session_id=session_id,
        agent_id=to_agent,
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
    checkpoint_id = f"ckpt_{uuid.uuid4().hex[:16]}"
    # Persist checkpoint label as session-scoped state
    from ocp_server.models import Scope, StateEntry
    entry = StateEntry(
        key=f"_checkpoint.{checkpoint_id}",
        value={"label": label, "session_id": session_id},
        scope=Scope.session,
        session_id=session_id,
        workspace_id=None,
    )
    # workspace_id may be None here — coordination tools tolerate that
    await store.state_set(entry)
    return {"checkpoint_id": checkpoint_id}


async def session_restore(store: BaseStore, checkpoint_id: str) -> dict:
    """Restore a session from a checkpoint — §4.4."""
    ckpt = await store.get_checkpoint(checkpoint_id)
    if ckpt is None:
        raise SessionNotFoundError(f"checkpoint:{checkpoint_id}")

    src_session_id = ckpt["session_id"]
    new_session_id = str(uuid.uuid4())

    # Materialise the new session in the same workspace
    src_row = await store.session_exists(src_session_id)
    # Best-effort: get workspace_id from source state entries
    entries, _ = await store.state_list(None, None, None, src_session_id, None, None)
    workspace_id = entries[0].workspace_id if entries else None

    await store.session_open(workspace_id or "", new_session_id, None, {})
    await store.copy_session_state(src_session_id, new_session_id)

    return {"session_id": new_session_id}


class SessionNotFoundError(Exception):
    code = "SESSION_NOT_FOUND"

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session not found: {session_id}")
