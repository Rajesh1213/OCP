"""§4.5 / §7 — Events tools and emission helpers.

Fixes:
  B10 — NotifyCallback is a proper Callable type alias, not type(None)
  S5  — events_subscribe with since= replays missed events in the response
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable

from ocp_server.storage.base import BaseStore

# B10: correct type alias for the notification callback
NotifyCallback = Callable[[str, dict], Awaitable[None]]


async def events_subscribe(
    store: BaseStore,
    workspace_id: str,
    types: list[str] | None,
    session_id: str | None,
    since: str | None,
) -> dict:
    subscription_id = await store.create_subscription(workspace_id, types, session_id)

    # S5: replay missed events if since is provided (§7.3)
    replayed: list[dict] = []
    if since:
        raw = await store.list_events_for_workspace(workspace_id, since)
        for ev in raw:
            payload = json.loads(ev["payload"]) if isinstance(ev["payload"], str) else ev["payload"]
            # honour type filter
            if types and ev["type"] not in types:
                continue
            replayed.append({
                "type": ev["type"],
                "event_id": ev["event_id"],
                "timestamp": ev["timestamp"],
                "workspace_id": ev["workspace_id"],
                "subscription_id": subscription_id,
                "payload": payload,
            })

    result: dict = {"subscription_id": subscription_id}
    if replayed:
        result["replayed"] = replayed
    return result


async def events_unsubscribe(store: BaseStore, subscription_id: str) -> dict:
    unsubscribed = await store.delete_subscription(subscription_id)
    return {"unsubscribed": unsubscribed}


async def emit_event(
    store: BaseStore,
    workspace_id: str,
    event_type: str,
    payload: dict,
    notify_callback: NotifyCallback | None = None,
) -> None:
    """Emit an event to all matching subscribers for a workspace."""
    subscriptions = await store.get_subscriptions_for_workspace(workspace_id)
    for sub in subscriptions:
        allowed = json.loads(sub["types"]) if sub.get("types") else None
        if allowed and event_type not in allowed:
            continue
        event_id = await store.append_event(
            workspace_id, sub["subscription_id"], event_type, payload
        )
        if notify_callback:
            import datetime
            envelope = {
                "type": event_type,
                "event_id": event_id,
                "timestamp": (datetime.datetime.now(datetime.timezone.utc)
                              .isoformat().replace("+00:00", "Z")),
                "workspace_id": workspace_id,
                "subscription_id": sub["subscription_id"],
                "payload": payload,
            }
            await notify_callback(sub["subscription_id"], envelope)
