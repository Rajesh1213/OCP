"""Tests for ocp-trainer exporter — all run against a real in-memory SQLite DB."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import aiosqlite


# ------------------------------------------------------------------ #
# Fixtures                                                             #
# ------------------------------------------------------------------ #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_traces (
    trace_id          TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    raw_prompt        TEXT NOT NULL,
    optimized_prompt  TEXT NOT NULL,
    result            TEXT,
    target_model      TEXT,
    workspace_id      TEXT,
    session_id        TEXT,
    raw_tokens        INTEGER NOT NULL DEFAULT 0,
    optimized_tokens  INTEGER NOT NULL DEFAULT 0
);
"""


async def _make_db(tmp_path: Path, rows: list[dict]) -> str:
    db_path = str(tmp_path / "test.db")
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(_SCHEMA)
        for row in rows:
            await db.execute(
                """INSERT INTO prompt_traces
                   (trace_id, created_at, raw_prompt, optimized_prompt, result,
                    target_model, workspace_id, session_id, raw_tokens, optimized_tokens)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["trace_id"], row["created_at"], row["raw_prompt"],
                    row["optimized_prompt"], row.get("result"), row.get("target_model"),
                    row.get("workspace_id"), row.get("session_id"),
                    row.get("raw_tokens", 0), row.get("optimized_tokens", 0),
                ),
            )
        await db.commit()
    return db_path


def _sample_rows(n: int = 5, completed: bool = True, offset: int = 0) -> list[dict]:
    return [
        {
            "trace_id": f"t{offset + i}",
            "created_at": f"2026-05-{i+1:02d}T10:00:00Z",
            "raw_prompt": f"Can you please explain in great detail how feature {i} works?",
            "optimized_prompt": f"Explain feature {i}.",
            "result": f"Feature {i} works by..." if completed else None,
            "target_model": "claude",
            "workspace_id": "ws-1",
            "session_id": None,
            "raw_tokens": 100,
            "optimized_tokens": 20,
        }
        for i in range(n)
    ]


# ------------------------------------------------------------------ #
# export_dataset                                                        #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_export_alpaca_format(tmp_path: Path):
    from ocp_trainer.exporter import export_dataset

    db_path = await _make_db(tmp_path, _sample_rows(3))
    out = str(tmp_path / "out.jsonl")
    count = await export_dataset(db_path, out, fmt="alpaca")

    assert count == 3
    lines = Path(out).read_text().splitlines()
    assert len(lines) == 3
    rec = json.loads(lines[0])
    assert "instruction" in rec
    assert "input" in rec
    assert "output" in rec


@pytest.mark.asyncio
async def test_export_chatml_format(tmp_path: Path):
    from ocp_trainer.exporter import export_dataset

    db_path = await _make_db(tmp_path, _sample_rows(2))
    out = str(tmp_path / "out.jsonl")
    await export_dataset(db_path, out, fmt="chatml")

    rec = json.loads(Path(out).read_text().splitlines()[0])
    assert "messages" in rec
    roles = [m["role"] for m in rec["messages"]]
    assert roles == ["system", "user", "assistant"]


@pytest.mark.asyncio
async def test_export_openai_format(tmp_path: Path):
    from ocp_trainer.exporter import export_dataset

    db_path = await _make_db(tmp_path, _sample_rows(2))
    out = str(tmp_path / "out.jsonl")
    await export_dataset(db_path, out, fmt="openai")

    rec = json.loads(Path(out).read_text().splitlines()[0])
    assert "messages" in rec


@pytest.mark.asyncio
async def test_export_only_completed_by_default(tmp_path: Path):
    from ocp_trainer.exporter import export_dataset

    rows = _sample_rows(3) + _sample_rows(2, completed=False, offset=3)
    db_path = await _make_db(tmp_path, rows)
    out = str(tmp_path / "out.jsonl")
    count = await export_dataset(db_path, out)

    assert count == 3


@pytest.mark.asyncio
async def test_export_include_incomplete(tmp_path: Path):
    from ocp_trainer.exporter import export_dataset

    rows = _sample_rows(3) + _sample_rows(2, completed=False, offset=3)
    db_path = await _make_db(tmp_path, rows)
    out = str(tmp_path / "out.jsonl")
    count = await export_dataset(db_path, out, only_completed=False)

    assert count == 5


@pytest.mark.asyncio
async def test_export_workspace_filter(tmp_path: Path):
    from ocp_trainer.exporter import export_dataset

    rows = _sample_rows(3)
    rows[0]["workspace_id"] = "ws-other"
    db_path = await _make_db(tmp_path, rows)
    out = str(tmp_path / "out.jsonl")
    count = await export_dataset(db_path, out, workspace_id="ws-1")

    assert count == 2


@pytest.mark.asyncio
async def test_export_unknown_format_raises(tmp_path: Path):
    from ocp_trainer.exporter import export_dataset

    db_path = await _make_db(tmp_path, _sample_rows(1))
    with pytest.raises(ValueError, match="Unknown format"):
        await export_dataset(db_path, str(tmp_path / "out.jsonl"), fmt="bad")


# ------------------------------------------------------------------ #
# traces.stats via SQLiteStore                                         #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_trace_stats_token_savings(tmp_path: Path):
    from ocp_server.storage.sqlite import SQLiteStore
    from ocp_server.tools.traces import traces_stats

    store = SQLiteStore(str(tmp_path / "ocp.db"))
    await store.setup()

    for row in _sample_rows(5):
        await store.save_prompt_trace(row)

    stats = await traces_stats(store, workspace_id=None, since=None)

    assert stats["total_requests"] == 5
    assert stats["total_tokens_saved"] == 5 * 80   # 100 - 20 per trace
    assert stats["avg_compression_ratio"] == 5.0    # 100/20
    assert "estimated_cost_saved_usd" in stats
    assert stats["estimated_cost_saved_usd"] > 0
    assert "by_model" in stats
    assert "daily" in stats
    assert "finetune_ready" in stats


@pytest.mark.asyncio
async def test_trace_stats_cost_saved(tmp_path: Path):
    from ocp_server.storage.sqlite import SQLiteStore
    from ocp_server.tools.traces import traces_stats
    import os

    store = SQLiteStore(str(tmp_path / "ocp.db"))
    await store.setup()
    for row in _sample_rows(10):
        await store.save_prompt_trace(row)

    os.environ["OCP_COST_PER_1K_TOKENS"] = "0.02"
    try:
        stats = await traces_stats(store, workspace_id=None, since=None)
        tokens_saved = 10 * 80
        expected = round(tokens_saved / 1000 * 0.02, 4)
        assert stats["estimated_cost_saved_usd"] == expected
    finally:
        del os.environ["OCP_COST_PER_1K_TOKENS"]


@pytest.mark.asyncio
async def test_trace_stats_finetune_ready(tmp_path: Path):
    from ocp_server.storage.sqlite import SQLiteStore
    from ocp_server.tools.traces import traces_stats

    store = SQLiteStore(str(tmp_path / "ocp.db"))
    await store.setup()

    for row in _sample_rows(99):
        await store.save_prompt_trace(row)
    stats = await traces_stats(store, workspace_id=None, since=None)
    assert stats["finetune_ready"] is False

    extra = _sample_rows(1)
    extra[0]["trace_id"] = "t-extra"
    await store.save_prompt_trace(extra[0])
    stats = await traces_stats(store, workspace_id=None, since=None)
    assert stats["finetune_ready"] is True
