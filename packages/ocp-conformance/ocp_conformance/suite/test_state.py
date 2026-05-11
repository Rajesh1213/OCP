"""§4.3 / §5 — State tool conformance tests."""
import pytest
from ocp_client import OCPClient
from ocp_client.types import OCPError


@pytest.mark.asyncio
async def test_state_set_get_global(workspace):
    """state.set / state.get round-trip at global scope. §4.3"""
    ws, client, _ = workspace
    r = await client.state_set("conf.language", "python", scope="global", workspace_id=ws.workspace_id)
    assert r.version >= 1
    entry = await client.state_get("conf.language", scope="global", workspace_id=ws.workspace_id)
    assert entry is not None
    assert entry.value == "python"


@pytest.mark.asyncio
async def test_state_set_get_session(workspace):
    """state.set / state.get round-trip at session scope. §4.3"""
    ws, client, _ = workspace
    sess = await client.session_open(ws.workspace_id)
    await client.state_set("plan", {"step": 1}, scope="session",
                           workspace_id=ws.workspace_id, session_id=sess.session_id)
    entry = await client.state_get("plan", scope="session",
                                   workspace_id=ws.workspace_id, session_id=sess.session_id)
    assert entry is not None
    assert entry.value["step"] == 1


@pytest.mark.asyncio
async def test_state_set_get_agent(workspace):
    """state.set / state.get round-trip at agent scope. §4.3"""
    ws, client, _ = workspace
    await client.state_set("agent.memo", "remember this",
                           scope="agent", agent_id="agent-001")
    entry = await client.state_get("agent.memo", scope="agent", agent_id="agent-001")
    assert entry is not None
    assert entry.value == "remember this"


@pytest.mark.asyncio
async def test_scope_resolution_order(workspace):
    """state.get without scope MUST resolve agent → session → global. §5.1"""
    ws, client, _ = workspace
    sess = await client.session_open(ws.workspace_id)
    # Write at session and global
    await client.state_set("x", "global_val", scope="global", workspace_id=ws.workspace_id)
    await client.state_set("x", "session_val", scope="session",
                           workspace_id=ws.workspace_id, session_id=sess.session_id)
    # Without explicit scope, agent scope checked first (absent), then session wins
    entry = await client.state_get("x", workspace_id=ws.workspace_id, session_id=sess.session_id)
    assert entry is not None
    assert entry.value == "session_val"


@pytest.mark.asyncio
async def test_state_version_increments(workspace):
    """state.set MUST increment version monotonically. §3.2"""
    ws, client, _ = workspace
    r1 = await client.state_set("counter", 1, scope="global", workspace_id=ws.workspace_id)
    r2 = await client.state_set("counter", 2, scope="global", workspace_id=ws.workspace_id)
    assert r2.version > r1.version


@pytest.mark.asyncio
async def test_state_optimistic_concurrency(workspace):
    """state.set with if_version MUST return CONFLICT on mismatch. §4.3"""
    ws, client, _ = workspace
    r = await client.state_set("oc_key", "v1", scope="global", workspace_id=ws.workspace_id)
    with pytest.raises(OCPError) as exc_info:
        await client.state_set("oc_key", "v2", scope="global",
                               workspace_id=ws.workspace_id, if_version=999)
    assert exc_info.value.code == "CONFLICT"


@pytest.mark.asyncio
async def test_state_delete(workspace):
    """state.delete MUST remove the entry. §4.3"""
    ws, client, _ = workspace
    await client.state_set("to_delete", "bye", scope="global", workspace_id=ws.workspace_id)
    deleted = await client.state_delete("to_delete", scope="global", workspace_id=ws.workspace_id)
    assert deleted is True
    entry = await client.state_get("to_delete", scope="global", workspace_id=ws.workspace_id)
    assert entry is None


@pytest.mark.asyncio
async def test_state_list_prefix(workspace):
    """state.list with prefix MUST return only matching keys. §4.3"""
    ws, client, _ = workspace
    await client.state_set("ns.a", 1, scope="global", workspace_id=ws.workspace_id)
    await client.state_set("ns.b", 2, scope="global", workspace_id=ws.workspace_id)
    await client.state_set("other.c", 3, scope="global", workspace_id=ws.workspace_id)
    entries, _ = await client.state_list(prefix="ns.", workspace_id=ws.workspace_id)
    keys = {e.key for e in entries}
    assert "ns.a" in keys
    assert "ns.b" in keys
    assert "other.c" not in keys


@pytest.mark.asyncio
async def test_scope_invalid_agent_without_id(workspace):
    """state.set with agent scope but no agent_id MUST return SCOPE_INVALID. §4.6"""
    ws, client, _ = workspace
    with pytest.raises(OCPError) as exc_info:
        await client.state_set("x", 1, scope="agent")
    assert exc_info.value.code == "SCOPE_INVALID"
