"""§4.4 — Coordination tool conformance tests."""
import pytest
from ocp_client import OCPClient
from ocp_client.types import OCPError


@pytest.mark.asyncio
async def test_session_open_server_issued(workspace):
    """session.open without session_id MUST return a server-generated session_id. §4.4"""
    ws, client, _ = workspace
    sess = await client.session_open(ws.workspace_id)
    assert sess.session_id


@pytest.mark.asyncio
async def test_session_open_client_issued(workspace):
    """session.open with a client-provided session_id MUST accept it. §3.3"""
    ws, client, _ = workspace
    sess = await client.session_open(ws.workspace_id, session_id="my-session-42")
    assert sess.session_id == "my-session-42"


@pytest.mark.asyncio
async def test_session_open_idempotent(workspace):
    """Opening an already-open session MUST NOT error. §3.3"""
    ws, client, _ = workspace
    sess = await client.session_open(ws.workspace_id, session_id="idempotent-sess")
    sess2 = await client.session_open(ws.workspace_id, session_id="idempotent-sess")
    assert sess.session_id == sess2.session_id


@pytest.mark.asyncio
async def test_session_close(workspace):
    """session.close MUST return closed=True. §4.4"""
    ws, client, _ = workspace
    sess = await client.session_open(ws.workspace_id)
    closed = await client.session_close(sess.session_id)
    assert closed is True


@pytest.mark.asyncio
async def test_session_handoff_delivers_to_inbox(workspace):
    """session.handoff MUST deliver the message to to_agent's _inbox. §4.4"""
    ws, client, _ = workspace
    sess = await client.session_open(ws.workspace_id)
    result = await client.session_handoff(
        sess.session_id,
        from_agent="planner",
        to_agent="executor",
        message={"ready": True},
    )
    assert result.delivered is True
    assert result.handoff_id

    # executor reads inbox
    inbox = await client.state_get("_inbox", scope="agent", agent_id="executor")
    assert inbox is not None
    assert inbox.value["from_agent"] == "planner"
    assert inbox.value["message"]["ready"] is True


@pytest.mark.asyncio
async def test_session_handoff_unknown_session(workspace):
    """session.handoff with unknown session_id MUST return SESSION_NOT_FOUND. §4.6"""
    _, client, _ = workspace
    with pytest.raises(OCPError) as exc_info:
        await client.session_handoff("no-such-session", "a", "b", {})
    assert exc_info.value.code == "SESSION_NOT_FOUND"


@pytest.mark.asyncio
async def test_session_checkpoint_restore(workspace):
    """session.checkpoint and session.restore MUST succeed. §4.4"""
    ws, client, _ = workspace
    sess = await client.session_open(ws.workspace_id)
    ckpt = await client.session_checkpoint(sess.session_id, label="pre-exec")
    assert ckpt.checkpoint_id

    restored = await client.session_restore(ckpt.checkpoint_id)
    assert restored.session_id
