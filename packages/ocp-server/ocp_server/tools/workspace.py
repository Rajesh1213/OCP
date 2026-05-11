"""§4.1 — Workspace tools."""
from __future__ import annotations

from ocp_server.models import make_workspace_id
from ocp_server.storage.base import BaseStore


async def workspace_register(store: BaseStore, root_uri: str, name: str | None, metadata: dict) -> dict:
    workspace_id = make_workspace_id(root_uri)
    exists = await store.workspace_exists(workspace_id)
    if not exists:
        await store.create_workspace(workspace_id, root_uri, name, metadata or {})
    return {"workspace_id": workspace_id, "created": not exists}


async def workspace_invalidate(store: BaseStore, workspace_id: str, paths: list[str]) -> dict:
    if not await store.workspace_exists(workspace_id):
        raise WorkspaceNotFoundError(workspace_id)
    count = await store.invalidate_chunks_by_path(workspace_id, paths)
    return {"invalidated": count}


async def workspace_list_chunks(
    store: BaseStore, workspace_id: str, filters: dict | None, cursor: str | None
) -> dict:
    if not await store.workspace_exists(workspace_id):
        raise WorkspaceNotFoundError(workspace_id)
    chunks, next_cursor = await store.list_chunks(workspace_id, filters, cursor)
    return {"chunks": [c.model_dump() for c in chunks], "next_cursor": next_cursor}


class WorkspaceNotFoundError(Exception):
    code = "WORKSPACE_NOT_FOUND"

    def __init__(self, workspace_id: str) -> None:
        super().__init__(f"Workspace not found: {workspace_id}")
