"""§4.2 — Retrieval tools."""
from __future__ import annotations

from typing import Any

from ocp_server.models import Chunk
from ocp_server.storage.base import BaseStore
from ocp_server.tools.workspace import WorkspaceNotFoundError


async def context_search(
    store: BaseStore,
    embedder: Any,
    workspace_id: str,
    query: str,
    k: int,
    filters: dict | None,
) -> dict:
    if not await store.workspace_exists(workspace_id):
        raise WorkspaceNotFoundError(workspace_id)
    k = min(k, 20)
    query_embedding = await embedder.embed(query)
    results = await store.search_chunks(workspace_id, query_embedding, k, filters)
    chunks = [c.model_dump() for c, _ in results]
    scores = [float(s) for _, s in results]
    return {"chunks": chunks, "scores": scores}


async def context_get_chunk(store: BaseStore, chunk_id: str) -> dict:
    chunk = await store.get_chunk(chunk_id)
    if chunk is None:
        raise ChunkNotFoundError(chunk_id)
    if await store.is_chunk_stale(chunk_id):
        raise StaleError(chunk_id)
    return {"chunk": chunk.model_dump()}


async def context_pack(
    store: BaseStore,
    embedder: Any,
    tokenizer: Any,
    workspace_id: str,
    intent: str,
    budget_tokens: int,
    include_state: bool,
) -> dict:
    if not await store.workspace_exists(workspace_id):
        raise WorkspaceNotFoundError(workspace_id)

    query_embedding = await embedder.embed(intent)
    results = await store.search_chunks(workspace_id, query_embedding, 20, None)

    parts: list[str] = []
    chunks_used: list[str] = []
    used_tokens = 0

    for chunk, _ in results:
        snippet = f"[{chunk.source.uri}]\n{chunk.content}"
        tokens = tokenizer.count(snippet)
        if used_tokens + tokens > budget_tokens:
            break
        parts.append(snippet)
        chunks_used.append(chunk.id)
        used_tokens += tokens

    if include_state:
        state_entries, _ = await store.state_list(None, None, workspace_id, None, None, None)
        state_used = []
        for entry in state_entries:
            line = f"[state:{entry.scope.value}:{entry.key}] {entry.value}"
            tokens = tokenizer.count(line)
            if used_tokens + tokens > budget_tokens:
                break
            parts.append(line)
            state_used.append(entry.key)
            used_tokens += tokens
    else:
        state_used = []

    if not parts:
        raise BudgetExceededError(budget_tokens)

    return {
        "context": "\n\n".join(parts),
        "chunks_used": chunks_used,
        "state_used": state_used,
        "tokens": used_tokens,
    }


class ChunkNotFoundError(Exception):
    code = "CHUNK_NOT_FOUND"

    def __init__(self, chunk_id: str) -> None:
        super().__init__(f"Chunk not found: {chunk_id}")


class StaleError(Exception):
    code = "STALE"

    def __init__(self, chunk_id: str) -> None:
        super().__init__(f"Chunk is stale: {chunk_id}")


class BudgetExceededError(Exception):
    code = "BUDGET_EXCEEDED"

    def __init__(self, budget: int) -> None:
        super().__init__(f"Cannot satisfy token budget: {budget}")
