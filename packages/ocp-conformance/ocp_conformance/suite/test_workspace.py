"""§4.1 — Workspace tool conformance tests."""
import pytest
from ocp_client.types import OCPError


@pytest.mark.asyncio
async def test_register_idempotent(workspace):
    """workspace.register with the same root_uri MUST return the same workspace_id. §4.1"""
    ws, client, tmp_path = workspace
    ws2 = await client.workspace_register(f"file://{tmp_path}", name="test")
    assert ws.workspace_id == ws2.workspace_id
    assert ws2.created is False


@pytest.mark.asyncio
async def test_register_new_workspace(ocp_client, tmp_path):
    """workspace.register with a new root_uri MUST return created=True. §4.1"""
    ws = await ocp_client.workspace_register(f"file://{tmp_path}/new", name="new")
    assert ws.created is True
    assert ws.workspace_id.startswith("ws_")


@pytest.mark.asyncio
async def test_index_returns_counts(workspace):
    """workspace.index MUST return indexed and skipped counts. §4.1"""
    ws, client, tmp_path = workspace
    result = await client.workspace_index(ws.workspace_id)
    assert result.indexed >= 0
    assert result.skipped >= 0
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_invalidate_idempotent(workspace):
    """workspace.invalidate is idempotent — calling twice MUST NOT error. §4.1"""
    ws, client, tmp_path = workspace
    await client.workspace_index(ws.workspace_id)
    r1 = await client.workspace_invalidate(ws.workspace_id, ["hello.py"])
    r2 = await client.workspace_invalidate(ws.workspace_id, ["hello.py"])
    assert r1.invalidated >= r2.invalidated


@pytest.mark.asyncio
async def test_invalidate_unknown_workspace(ocp_client):
    """workspace.invalidate with unknown workspace_id MUST return WORKSPACE_NOT_FOUND. §4.6"""
    with pytest.raises(OCPError) as exc_info:
        await ocp_client.workspace_invalidate("ws_nonexistent", ["/"])
    assert exc_info.value.code == "WORKSPACE_NOT_FOUND"


@pytest.mark.asyncio
async def test_list_chunks(workspace):
    """workspace.list_chunks MUST return chunks after indexing. §4.1"""
    ws, client, tmp_path = workspace
    await client.workspace_index(ws.workspace_id)
    chunks, _ = await client.workspace_list_chunks(ws.workspace_id)
    assert len(chunks) >= 1
