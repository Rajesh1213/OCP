"""§4.5 / §7 — Events tools and emission helpers."""
from __future__ import annotations

from ocp_server.storage.base import BaseStore


async def events_subscribe(
    store: BaseStore,
    workspace_id: str,
    types: list[str] | None,
    session_id: str | None,
    since: str | None,
) -> dict:
    subscription_id = await store.create_subscription(workspace_id, types, session_id)
    return {"subscription_id": subscription_id}


async def events_unsubscribe(store: BaseStore, subscription_id: str) -> dict:
    unsubscribed = await store.delete_subscription(subscription_id)
    return {"unsubscribed": unsubscribed}


async def emit_event(
    store: BaseStore,
    workspace_id: str,
    event_type: str,
    payload: dict,
    notify_callback: "NotifyCallback | None" = None,
) -> None:
    """Emit an event to all matching subscribers for a workspace."""
    subscriptions = await store.get_subscriptions_for_workspace(workspace_id)
    for sub in subscriptions:
        types = sub.get("types")
        if types:
            import json
            allowed = json.loads(types)
            if event_type not in allowed:
                continue
        event_id = await store.append_event(workspace_id, sub["subscription_id"], event_type, payload)
        if notify_callback:
            import datetime
            envelope = {
                "type": event_type,
                "event_id": event_id,
                "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                "workspace_id": workspace_id,
                "subscription_id": sub["subscription_id"],
                "payload": payload,
            }
            await notify_callback(sub["subscription_id"], envelope)


NotifyCallback = type(None)  # placeholder — replaced at server wiring time
