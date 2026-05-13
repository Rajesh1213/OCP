"""§4.5 / §7 — Events tool conformance tests."""
import pytest


@pytest.mark.asyncio
async def test_subscribe_returns_subscription_id(workspace):
    """events.subscribe MUST return a subscription_id. §4.5"""
    ws, client, _ = workspace
    sub = await client.events_subscribe(ws.workspace_id)
    assert sub.subscription_id.startswith("sub_")


@pytest.mark.asyncio
async def test_unsubscribe(workspace):
    """events.unsubscribe MUST return unsubscribed=True for a live subscription. §4.5"""
    ws, client, _ = workspace
    sub = await client.events_subscribe(ws.workspace_id)
    ok = await client.events_unsubscribe(sub.subscription_id)
    assert ok is True


@pytest.mark.asyncio
async def test_unsubscribe_idempotent(workspace):
    """events.unsubscribe on an already-removed subscription MUST NOT error. §4.5"""
    ws, client, _ = workspace
    sub = await client.events_subscribe(ws.workspace_id)
    await client.events_unsubscribe(sub.subscription_id)
    ok = await client.events_unsubscribe(sub.subscription_id)
    assert ok is False


@pytest.mark.asyncio
async def test_subscribe_with_type_filter(workspace):
    """events.subscribe with types filter MUST be accepted. §4.5"""
    ws, client, _ = workspace
    sub = await client.events_subscribe(ws.workspace_id, types=["chunk.invalidated"])
    assert sub.subscription_id


@pytest.mark.asyncio
async def test_subscribe_with_session(workspace):
    """events.subscribe with session_id MUST be accepted. §4.5"""
    ws, client, _ = workspace
    sess = await client.session_open(ws.workspace_id)
    sub = await client.events_subscribe(ws.workspace_id, session_id=sess.session_id)
    assert sub.subscription_id
