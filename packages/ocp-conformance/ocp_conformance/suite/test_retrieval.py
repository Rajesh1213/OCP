"""§4.2 — Retrieval tool conformance tests."""
import pytest
from ocp_client.types import OCPError


@pytest.mark.asyncio
async def test_search_returns_chunks_and_scores(workspace):
    """context.search MUST return chunks and scores of equal length. §4.2"""
    ws, client, tmp_path = workspace
    await client.workspace_index(ws.workspace_id)
    result = await client.context_search(ws.workspace_id, "hello function")
    assert len(result.chunks) == len(result.scores)
    assert len(result.chunks) >= 1


@pytest.mark.asyncio
async def test_search_k_cap(workspace):
    """context.search MUST NOT return more chunks than k. §4.2"""
    ws, client, tmp_path = workspace
    await client.workspace_index(ws.workspace_id)
    result = await client.context_search(ws.workspace_id, "hello", k=1)
    assert len(result.chunks) <= 1


@pytest.mark.asyncio
async def test_get_chunk_by_id(workspace):
    """context.get_chunk MUST return the chunk for a valid ID. §4.2"""
    ws, client, tmp_path = workspace
    await client.workspace_index(ws.workspace_id)
    result = await client.context_search(ws.workspace_id, "hello", k=1)
    assert result.chunks
    chunk_id = result.chunks[0].id
    chunk = await client.context_get_chunk(chunk_id)
    assert chunk.id == chunk_id
    assert chunk.content


@pytest.mark.asyncio
async def test_get_chunk_not_found(ocp_client):
    """context.get_chunk MUST return CHUNK_NOT_FOUND for unknown id. §4.6"""
    with pytest.raises(OCPError) as exc_info:
        await ocp_client.context_get_chunk("chk_doesnotexist000000")
    assert exc_info.value.code == "CHUNK_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_chunk_stale_after_invalidation(workspace):
    """context.get_chunk MUST return STALE after workspace.invalidate. §4.2 §6.2"""
    ws, client, tmp_path = workspace
    await client.workspace_index(ws.workspace_id)
    result = await client.context_search(ws.workspace_id, "hello", k=1)
    assert result.chunks
    chunk_id = result.chunks[0].id

    await client.workspace_invalidate(ws.workspace_id, [str(tmp_path / "hello.py")])

    with pytest.raises(OCPError) as exc_info:
        await client.context_get_chunk(chunk_id)
    assert exc_info.value.code == "STALE"


@pytest.mark.asyncio
async def test_search_excludes_stale_chunks(workspace):
    """context.search MUST NOT include stale chunks. §6.2"""
    ws, client, tmp_path = workspace
    await client.workspace_index(ws.workspace_id)
    before = await client.context_search(ws.workspace_id, "hello")
    await client.workspace_invalidate(ws.workspace_id, [str(tmp_path / "hello.py")])
    after = await client.context_search(ws.workspace_id, "hello")
    ids_after = {c.id for c in after.chunks}

    # stale chunks from hello.py must not appear in results
    stale_py_ids = {c.id for c in before.chunks if "hello.py" in (c.source.uri if hasattr(c, "source") else "")}
    assert not stale_py_ids.intersection(ids_after)


@pytest.mark.asyncio
async def test_pack_respects_budget(workspace):
    """context.pack MUST stay within budget_tokens. §4.2"""
    ws, client, tmp_path = workspace
    await client.workspace_index(ws.workspace_id)
    result = await client.context_pack(ws.workspace_id, "hello", budget_tokens=500)
    assert result.tokens <= 500
    assert result.context
    assert result.chunks_used
