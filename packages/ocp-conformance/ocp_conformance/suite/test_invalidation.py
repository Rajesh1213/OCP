"""§6 — Invalidation contract conformance tests (previously missing)."""
import pytest
from ocp_client.types import OCPError


@pytest.mark.asyncio
async def test_get_chunk_stale_after_invalidation(workspace):
    """context.get_chunk MUST return STALE after workspace.invalidate. §6.2"""
    ws, client, tmp_path = workspace
    await client.workspace_index(ws.workspace_id)

    results = await client.context_search(ws.workspace_id, "hello world", k=1)
    assert results.chunks, "Need at least one indexed chunk"
    chunk_id = results.chunks[0].id

    # Invalidate all paths
    await client.workspace_invalidate(ws.workspace_id, [str(tmp_path)])

    with pytest.raises(OCPError) as exc_info:
        await client.context_get_chunk(chunk_id)
    assert exc_info.value.code == "STALE"


@pytest.mark.asyncio
async def test_invalidate_returns_chunk_ids(workspace):
    """workspace.invalidate response must include invalidated count > 0 after indexing. §4.1 §B4"""
    ws, client, tmp_path = workspace
    idx = await client.workspace_index(ws.workspace_id)
    assert idx.indexed > 0

    result = await client.workspace_invalidate(ws.workspace_id, [str(tmp_path)])
    assert result.invalidated > 0


@pytest.mark.asyncio
async def test_search_excludes_stale_after_invalidation(workspace):
    """context.search MUST NOT return stale chunks. §6.2"""
    ws, client, tmp_path = workspace
    await client.workspace_index(ws.workspace_id)

    before = await client.context_search(ws.workspace_id, "hello")
    assert before.chunks

    await client.workspace_invalidate(ws.workspace_id, [str(tmp_path)])

    after = await client.context_search(ws.workspace_id, "hello")
    before_ids = {c.id for c in before.chunks}
    after_ids = {c.id for c in after.chunks}
    assert before_ids.isdisjoint(after_ids), "Stale chunks must not appear in search"


@pytest.mark.asyncio
async def test_reindex_clears_stale(workspace):
    """Re-indexing MUST produce a fresh chunk that is no longer stale. §6.2"""
    ws, client, tmp_path = workspace
    await client.workspace_index(ws.workspace_id)

    results = await client.context_search(ws.workspace_id, "hello world", k=1)
    assert results.chunks
    await client.workspace_invalidate(ws.workspace_id, [str(tmp_path)])
    await client.workspace_index(ws.workspace_id)

    # After reindex, search should return results again
    results2 = await client.context_search(ws.workspace_id, "hello world", k=1)
    assert results2.chunks, "Fresh chunks must appear after reindex"
