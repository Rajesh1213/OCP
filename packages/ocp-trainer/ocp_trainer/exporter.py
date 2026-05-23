"""Export prompt_traces from an OCP SQLite database as a training dataset."""
from __future__ import annotations

import json
import os
from pathlib import Path


async def export_dataset(
    db_path: str,
    output_path: str,
    fmt: str = "alpaca",
    workspace_id: str | None = None,
    since: str | None = None,
    only_completed: bool = True,
) -> int:
    """Export traces to a JSONL file. Returns the number of records written."""
    import aiosqlite

    filters: list[str] = []
    params: list = []
    if only_completed:
        filters.append("result IS NOT NULL")
    if workspace_id:
        filters.append("workspace_id=?")
        params.append(workspace_id)
    if since:
        filters.append("created_at>=?")
        params.append(since)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM prompt_traces {where} ORDER BY created_at",
            params,
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    records = _format(rows, fmt)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")

    return len(records)


def _format(rows: list[dict], fmt: str) -> list[dict]:
    if fmt == "alpaca":
        return [
            {
                "instruction": "Compress and optimise this prompt for an AI language model.",
                "input": r["raw_prompt"],
                "output": r["optimized_prompt"],
            }
            for r in rows
        ]
    if fmt in ("chatml", "openai"):
        return [
            {
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a prompt optimizer. Compress and improve the given prompt.",
                    },
                    {"role": "user", "content": r["raw_prompt"]},
                    {"role": "assistant", "content": r["optimized_prompt"]},
                ]
            }
            for r in rows
        ]
    raise ValueError(f"Unknown format: {fmt}. Use alpaca, chatml, or openai.")


def print_stats(db_path: str) -> None:
    """Synchronous helper — print a quick stats summary to stdout."""
    import asyncio
    import aiosqlite

    async def _run() -> None:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END) AS completed,
                    COALESCE(SUM(raw_tokens), 0) AS raw_total,
                    COALESCE(SUM(optimized_tokens), 0) AS opt_total,
                    COALESCE(SUM(raw_tokens - optimized_tokens), 0) AS saved,
                    COALESCE(AVG(CASE WHEN optimized_tokens > 0
                        THEN CAST(raw_tokens AS REAL)/optimized_tokens END), 1.0) AS ratio
                FROM prompt_traces"""
            ) as cur:
                raw = await cur.fetchone()
                assert raw is not None
                row = dict(raw)

        rate = float(os.environ.get("OCP_COST_PER_1K_TOKENS", "0.01"))
        cost = round(row["saved"] / 1000 * rate, 4)
        print(f"Total requests   : {row['total']}")
        print(f"Completed traces : {row['completed']}")
        print(f"Tokens (raw)     : {row['raw_total']:,}")
        print(f"Tokens (optimised): {row['opt_total']:,}")
        print(f"Tokens saved     : {row['saved']:,}")
        print(f"Avg compression  : {row['ratio']:.2f}x")
        print(f"Est. cost saved  : ${cost:.4f}  (at ${rate}/1k tokens)")
        needed = 100 - row["completed"]
        ready_str = "yes" if row["completed"] >= 100 else f"no — need {needed} more completed traces"
        print(f"Fine-tune ready  : {ready_str}")

    asyncio.run(_run())
