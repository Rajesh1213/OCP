"""ocp.traces.stats and ocp.traces.export tools."""
from __future__ import annotations

import os
from typing import Any

from ocp_server.storage.base import BaseStore

_DEFAULT_COST_PER_1K = 0.01  # USD — override with OCP_COST_PER_1K_TOKENS


def _cost(tokens_saved: int) -> float:
    rate = float(os.environ.get("OCP_COST_PER_1K_TOKENS", _DEFAULT_COST_PER_1K))
    return round(tokens_saved / 1000 * rate, 4)


async def traces_stats(
    store: BaseStore,
    workspace_id: str | None,
    since: str | None,
) -> dict:
    stats = await store.get_trace_stats(workspace_id=workspace_id, since=since)

    tokens_saved = stats["total_tokens_saved"]
    stats["estimated_cost_saved_usd"] = _cost(tokens_saved)

    for model_data in stats["by_model"].values():
        model_data["cost_saved_usd"] = _cost(model_data["tokens_saved"])

    for ws_data in stats["by_workspace"].values():
        ws_data["cost_saved_usd"] = _cost(ws_data["tokens_saved"])

    for day in stats["daily"]:
        day["cost_saved_usd"] = _cost(day["tokens_saved"])

    return stats


async def traces_export(
    store: BaseStore,
    fmt: str,
    workspace_id: str | None,
    since: str | None,
    only_completed: bool,
) -> dict[str, Any]:
    records = await store.export_traces(
        fmt=fmt,
        workspace_id=workspace_id,
        since=since,
        only_completed=only_completed,
    )
    return {
        "format": fmt,
        "count": len(records),
        "records": records,
    }
