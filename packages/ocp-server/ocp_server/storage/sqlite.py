"""SQLite-backed OCP store with cosine-similarity vector search.

Fixes applied:
  B2  — optimistic concurrency uses explicit if_version parameter
  B4  — invalidate_chunks_by_path returns chunk IDs via RETURNING
  B6  — asyncio.Lock serialises all write operations
  B7  — _now() uses timezone-aware datetime (no deprecated utcnow)
  S1  — state_delete raises ConflictError on if_version mismatch
  S3  — state_set auto-materialises a session row when scope=session
"""
from __future__ import annotations

import asyncio
import datetime
import json
import math
import uuid
from typing import Any

import aiosqlite

from ocp_server.models import Chunk, ChunkSource, Scope, SourceRange, StateEntry
from ocp_server.storage.base import BaseStore

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    root_uri     TEXT NOT NULL,
    name         TEXT,
    metadata     TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    source_uri   TEXT NOT NULL,
    source_range TEXT,
    content_hash TEXT NOT NULL,
    kind         TEXT NOT NULL,
    language     TEXT,
    symbol       TEXT,
    content      TEXT NOT NULL,
    metadata     TEXT NOT NULL DEFAULT '{}',
    version      INTEGER NOT NULL DEFAULT 1,
    stale        INTEGER NOT NULL DEFAULT 0,
    embedding    BLOB,
    FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_workspace ON chunks(workspace_id);
CREATE INDEX IF NOT EXISTS idx_chunks_uri       ON chunks(source_uri);

CREATE TABLE IF NOT EXISTS state (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT NOT NULL,
    value        TEXT NOT NULL,
    scope        TEXT NOT NULL,
    workspace_id TEXT,
    session_id   TEXT,
    agent_id     TEXT,
    ttl_seconds  INTEGER,
    updated_at   TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    UNIQUE(key, scope, workspace_id, session_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_state_lookup
    ON state(scope, workspace_id, session_id, agent_id, key);

CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    ttl_seconds  INTEGER,
    metadata     TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    closed       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subscriptions (
    subscription_id TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    session_id      TEXT,
    types           TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL,
    workspace_id    TEXT NOT NULL,
    type            TEXT NOT NULL,
    payload         TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions(subscription_id)
);

CREATE INDEX IF NOT EXISTS idx_events_sub ON events(subscription_id, event_id);
CREATE INDEX IF NOT EXISTS idx_events_ws  ON events(workspace_id, event_id);
"""


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _now() -> str:
    # B7: use timezone-aware datetime, not deprecated utcnow()
    return (datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"))


def _pack_embedding(v: list[float]) -> bytes:
    import struct
    return struct.pack(f"{len(v)}f", *v)


def _unpack_embedding(b: bytes) -> list[float]:
    import struct
    n = len(b) // 4
    return list(struct.unpack(f"{n}f", b))


class SQLiteStore(BaseStore):

    def __init__(self, db_path: str = "ocp.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        # B6: single asyncio.Lock serialises all write operations
        self._write_lock = asyncio.Lock()

    async def setup(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Store not initialized — call setup() first")
        return self._db

    # ------------------------------------------------------------------ #
    # Workspace                                                            #
    # ------------------------------------------------------------------ #

    async def workspace_exists(self, workspace_id: str) -> bool:
        db = await self._conn()
        async with db.execute(
            "SELECT 1 FROM workspaces WHERE workspace_id=?", (workspace_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def create_workspace(
        self, workspace_id: str, root_uri: str, name: str | None, metadata: dict
    ) -> None:
        async with self._write_lock:
            db = await self._conn()
            await db.execute(
                "INSERT OR IGNORE INTO workspaces(workspace_id,root_uri,name,metadata) VALUES(?,?,?,?)",
                (workspace_id, root_uri, name, json.dumps(metadata)),
            )
            await db.commit()

    async def get_workspace_root(self, workspace_id: str) -> str | None:
        db = await self._conn()
        async with db.execute(
            "SELECT root_uri FROM workspaces WHERE workspace_id=?", (workspace_id,)
        ) as cur:
            row = await cur.fetchone()
        return row["root_uri"] if row else None

    async def list_all_workspaces(self) -> list[dict]:
        db = await self._conn()
        async with db.execute("SELECT * FROM workspaces") as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Chunks                                                               #
    # ------------------------------------------------------------------ #

    async def upsert_chunk(self, chunk: Chunk, embedding: list[float]) -> None:
        async with self._write_lock:
            db = await self._conn()
            range_json = chunk.source.range.model_dump_json() if chunk.source.range else None
            emb_blob = _pack_embedding(embedding)
            await db.execute(
                """INSERT INTO chunks
                   (chunk_id,workspace_id,source_uri,source_range,content_hash,kind,
                    language,symbol,content,metadata,version,stale,embedding)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?)
                   ON CONFLICT(chunk_id) DO UPDATE SET
                     content=excluded.content,
                     content_hash=excluded.content_hash,
                     version=version+1,
                     stale=0,
                     embedding=excluded.embedding,
                     metadata=excluded.metadata""",
                (
                    chunk.id, chunk.workspace_id, chunk.source.uri, range_json,
                    chunk.source.content_hash, chunk.kind, chunk.language, chunk.symbol,
                    chunk.content, json.dumps(chunk.metadata), chunk.version, emb_blob,
                ),
            )
            await db.commit()

    async def get_chunk(self, chunk_id: str) -> Chunk | None:
        db = await self._conn()
        async with db.execute(
            "SELECT * FROM chunks WHERE chunk_id=?", (chunk_id,)
        ) as cur:
            row = await cur.fetchone()
        return _row_to_chunk(row) if row else None

    async def is_chunk_stale(self, chunk_id: str) -> bool:
        db = await self._conn()
        async with db.execute(
            "SELECT stale FROM chunks WHERE chunk_id=?", (chunk_id,)
        ) as cur:
            row = await cur.fetchone()
        return bool(row["stale"]) if row else False

    async def invalidate_chunks_by_path(
        self, workspace_id: str, paths: list[str]
    ) -> list[str]:
        # B4: return chunk IDs via RETURNING clause instead of a count
        async with self._write_lock:
            db = await self._conn()
            chunk_ids: list[str] = []
            for path in paths:
                pattern = path if path.startswith("file://") else f"file://{path}"
                async with db.execute(
                    """UPDATE chunks SET stale=1
                       WHERE workspace_id=? AND source_uri LIKE ? AND stale=0
                       RETURNING chunk_id""",
                    (workspace_id, f"{pattern}%"),
                ) as cur:
                    rows = await cur.fetchall()
                    chunk_ids.extend(r[0] for r in rows)
            await db.commit()
        return chunk_ids

    async def search_chunks(
        self,
        workspace_id: str,
        query_embedding: list[float],
        k: int,
        filters: dict[str, Any] | None,
    ) -> list[tuple[Chunk, float]]:
        db = await self._conn()
        where = "workspace_id=? AND stale=0 AND embedding IS NOT NULL"
        params: list[Any] = [workspace_id]
        if filters:
            if "kind" in filters:
                where += " AND kind=?"
                params.append(filters["kind"])
            if "language" in filters:
                where += " AND language=?"
                params.append(filters["language"])
        async with db.execute(
            f"SELECT * FROM chunks WHERE {where}", params
        ) as cur:
            rows = await cur.fetchall()
        scored = [
            (_row_to_chunk(row), _cosine(query_embedding, _unpack_embedding(row["embedding"])))
            for row in rows
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:k]

    async def list_chunks(
        self, workspace_id: str, filters: dict | None, cursor: str | None
    ) -> tuple[list[Chunk], str | None]:
        db = await self._conn()
        offset = int(cursor) if cursor else 0
        page = 50
        async with db.execute(
            "SELECT * FROM chunks WHERE workspace_id=? LIMIT ? OFFSET ?",
            (workspace_id, page + 1, offset),
        ) as cur:
            rows = await cur.fetchall()
        next_cursor = str(offset + page) if len(rows) > page else None
        return [_row_to_chunk(r) for r in rows[:page]], next_cursor

    # ------------------------------------------------------------------ #
    # State                                                                #
    # ------------------------------------------------------------------ #

    async def state_set(self, entry: StateEntry, if_version: int | None = None) -> int:
        # B2: explicit if_version parameter — no more version-encoding tricks
        # S3: auto-materialise session row if scope=session and session unknown
        async with self._write_lock:
            db = await self._conn()

            if if_version is not None:
                async with db.execute(
                    """SELECT version FROM state
                       WHERE key=? AND scope=?
                         AND workspace_id IS ? AND session_id IS ? AND agent_id IS ?""",
                    (entry.key, entry.scope.value,
                     entry.workspace_id, entry.session_id, entry.agent_id),
                ) as cur:
                    row = await cur.fetchone()
                current = row["version"] if row else 0
                if current != if_version:
                    raise ConflictError(
                        f"version mismatch: expected {if_version}, got {current}"
                    )

            # S3: lazy session materialisation
            if entry.scope == Scope.session and entry.session_id:
                async with db.execute(
                    "SELECT 1 FROM sessions WHERE session_id=?", (entry.session_id,)
                ) as cur:
                    exists = await cur.fetchone()
                if not exists:
                    await db.execute(
                        """INSERT OR IGNORE INTO sessions
                           (session_id,workspace_id,ttl_seconds,metadata,created_at)
                           VALUES(?,?,NULL,'{}',?)""",
                        (entry.session_id, entry.workspace_id or "", _now()),
                    )

            now = _now()
            async with db.execute(
                """INSERT INTO state
                   (key,value,scope,workspace_id,session_id,agent_id,
                    ttl_seconds,updated_at,version)
                   VALUES(?,?,?,?,?,?,?,?,1)
                   ON CONFLICT(key,scope,workspace_id,session_id,agent_id) DO UPDATE SET
                     value=excluded.value,
                     ttl_seconds=excluded.ttl_seconds,
                     updated_at=excluded.updated_at,
                     version=version+1
                   RETURNING version""",
                (
                    entry.key, json.dumps(entry.value), entry.scope.value,
                    entry.workspace_id, entry.session_id, entry.agent_id,
                    entry.ttl_seconds, now,
                ),
            ) as cur:
                row = await cur.fetchone()
            await db.commit()
        return row["version"] if row else 1

    async def state_get(
        self, key: str, scope: Scope,
        workspace_id: str | None, session_id: str | None, agent_id: str | None,
    ) -> StateEntry | None:
        db = await self._conn()
        async with db.execute(
            """SELECT * FROM state
               WHERE key=? AND scope=?
                 AND workspace_id IS ? AND session_id IS ? AND agent_id IS ?""",
            (key, scope.value, workspace_id, session_id, agent_id),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_state(row) if row else None

    async def state_list(
        self,
        prefix: str | None, scope: Scope | None,
        workspace_id: str | None, session_id: str | None, agent_id: str | None,
        cursor: str | None,
    ) -> tuple[list[StateEntry], str | None]:
        db = await self._conn()
        offset = int(cursor) if cursor else 0
        page = 100
        where_parts = []
        params: list[Any] = []
        if prefix:
            where_parts.append("key LIKE ?")
            params.append(f"{prefix}%")
        if scope:
            where_parts.append("scope=?")
            params.append(scope.value)
        if workspace_id is not None:
            where_parts.append("workspace_id IS ?")
            params.append(workspace_id)
        if session_id is not None:
            where_parts.append("session_id IS ?")
            params.append(session_id)
        if agent_id is not None:
            where_parts.append("agent_id IS ?")
            params.append(agent_id)
        where = " AND ".join(where_parts) if where_parts else "1"
        params += [page + 1, offset]
        async with db.execute(
            f"SELECT * FROM state WHERE {where} LIMIT ? OFFSET ?", params
        ) as cur:
            rows = await cur.fetchall()
        next_cursor = str(offset + page) if len(rows) > page else None
        return [_row_to_state(r) for r in rows[:page]], next_cursor

    async def state_delete(
        self, key: str, scope: Scope,
        workspace_id: str | None, session_id: str | None, agent_id: str | None,
        if_version: int | None,
    ) -> bool:
        # S1: raise ConflictError on if_version mismatch instead of silent false
        async with self._write_lock:
            db = await self._conn()
            if if_version is not None:
                async with db.execute(
                    """SELECT version FROM state
                       WHERE key=? AND scope=?
                         AND workspace_id IS ? AND session_id IS ? AND agent_id IS ?""",
                    (key, scope.value, workspace_id, session_id, agent_id),
                ) as cur:
                    row = await cur.fetchone()
                if row is None:
                    return False  # entry doesn't exist — nothing to conflict with
                if row["version"] != if_version:
                    raise ConflictError(
                        f"delete version mismatch: expected {if_version}, got {row['version']}"
                    )
            async with db.execute(
                """DELETE FROM state
                   WHERE key=? AND scope=?
                     AND workspace_id IS ? AND session_id IS ? AND agent_id IS ?
                   RETURNING 1""",
                (key, scope.value, workspace_id, session_id, agent_id),
            ) as cur:
                row = await cur.fetchone()
            await db.commit()
        return row is not None

    # ------------------------------------------------------------------ #
    # Sessions                                                             #
    # ------------------------------------------------------------------ #

    async def session_open(
        self, workspace_id: str, session_id: str,
        ttl_seconds: int | None, metadata: dict,
    ) -> None:
        async with self._write_lock:
            db = await self._conn()
            await db.execute(
                """INSERT OR IGNORE INTO sessions
                   (session_id,workspace_id,ttl_seconds,metadata,created_at)
                   VALUES(?,?,?,?,?)""",
                (session_id, workspace_id, ttl_seconds, json.dumps(metadata), _now()),
            )
            await db.commit()

    async def session_close(self, session_id: str) -> bool:
        async with self._write_lock:
            db = await self._conn()
            async with db.execute(
                "UPDATE sessions SET closed=1 WHERE session_id=? AND closed=0 RETURNING 1",
                (session_id,),
            ) as cur:
                row = await cur.fetchone()
            await db.commit()
        return row is not None

    async def session_exists(self, session_id: str) -> bool:
        db = await self._conn()
        async with db.execute(
            "SELECT 1 FROM sessions WHERE session_id=? AND closed=0", (session_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def get_session_workspace(self, session_id: str) -> str | None:
        # B1 helper: used by checkpoint/restore to get workspace_id
        db = await self._conn()
        async with db.execute(
            "SELECT workspace_id FROM sessions WHERE session_id=?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
        return row["workspace_id"] if row else None

    # ------------------------------------------------------------------ #
    # Events / subscriptions                                               #
    # ------------------------------------------------------------------ #

    async def create_subscription(
        self, workspace_id: str, types: list[str] | None, session_id: str | None
    ) -> str:
        async with self._write_lock:
            db = await self._conn()
            sub_id = f"sub_{uuid.uuid4().hex[:12]}"
            await db.execute(
                """INSERT INTO subscriptions
                   (subscription_id,workspace_id,session_id,types,created_at)
                   VALUES(?,?,?,?,?)""",
                (sub_id, workspace_id, session_id,
                 json.dumps(types) if types else None, _now()),
            )
            await db.commit()
        return sub_id

    async def delete_subscription(self, subscription_id: str) -> bool:
        async with self._write_lock:
            db = await self._conn()
            async with db.execute(
                "DELETE FROM subscriptions WHERE subscription_id=? RETURNING 1",
                (subscription_id,),
            ) as cur:
                row = await cur.fetchone()
            await db.commit()
        return row is not None

    async def get_subscriptions_for_workspace(self, workspace_id: str) -> list[dict]:
        db = await self._conn()
        async with db.execute(
            "SELECT * FROM subscriptions WHERE workspace_id=?", (workspace_id,)
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def append_event(
        self, workspace_id: str, subscription_id: str, event_type: str, payload: dict
    ) -> str:
        async with self._write_lock:
            db = await self._conn()
            event_id = f"evt_{uuid.uuid4().hex[:12]}"
            await db.execute(
                """INSERT INTO events
                   (event_id,subscription_id,workspace_id,type,payload,timestamp)
                   VALUES(?,?,?,?,?,?)""",
                (event_id, subscription_id, workspace_id,
                 event_type, json.dumps(payload), _now()),
            )
            await db.commit()
        return event_id

    async def list_events(self, subscription_id: str, since: str | None) -> list[dict]:
        db = await self._conn()
        if since:
            async with db.execute(
                """SELECT * FROM events
                   WHERE subscription_id=? AND event_id > ?
                   ORDER BY event_id LIMIT 1000""",
                (subscription_id, since),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute(
                """SELECT * FROM events
                   WHERE subscription_id=?
                   ORDER BY event_id LIMIT 1000""",
                (subscription_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_events_for_workspace(
        self, workspace_id: str, since: str
    ) -> list[dict]:
        # S5: used by events_subscribe with since= to replay missed events
        db = await self._conn()
        async with db.execute(
            """SELECT * FROM events
               WHERE workspace_id=? AND event_id > ?
               ORDER BY event_id LIMIT 1000""",
            (workspace_id, since),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Workspace helpers                                                    #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # TTL / maintenance                                                    #
    # ------------------------------------------------------------------ #

    async def purge_expired_state(self) -> int:
        async with self._write_lock:
            db = await self._conn()
            async with db.execute(
                """DELETE FROM state
                   WHERE ttl_seconds IS NOT NULL
                     AND ((julianday('now') - julianday(updated_at)) * 86400) > ttl_seconds
                   RETURNING 1"""
            ) as cur:
                expired_entries = len(await cur.fetchall())
            async with db.execute(
                """UPDATE sessions SET closed=1
                   WHERE ttl_seconds IS NOT NULL AND closed=0
                     AND ((julianday('now') - julianday(created_at)) * 86400) > ttl_seconds
                   RETURNING session_id"""
            ) as cur:
                expired_sessions = len(await cur.fetchall())
            await db.commit()
        return expired_entries + expired_sessions

    # ------------------------------------------------------------------ #
    # Checkpoint / restore                                                 #
    # ------------------------------------------------------------------ #

    async def get_checkpoint(self, checkpoint_id: str) -> dict | None:
        db = await self._conn()
        async with db.execute(
            "SELECT value, session_id FROM state WHERE key=?",
            (f"_checkpoint.{checkpoint_id}",),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "checkpoint_id": checkpoint_id,
            **json.loads(row["value"]),
            "session_id": row["session_id"],
        }

    async def copy_session_state(
        self, src_session_id: str, dst_session_id: str
    ) -> int:
        async with self._write_lock:
            db = await self._conn()
            async with db.execute(
                "SELECT * FROM state WHERE scope='session' AND session_id=?",
                (src_session_id,),
            ) as cur:
                rows = await cur.fetchall()
            count = 0
            now = _now()
            for row in rows:
                if row["key"].startswith("_checkpoint."):
                    continue
                await db.execute(
                    """INSERT INTO state
                       (key,value,scope,workspace_id,session_id,agent_id,
                        ttl_seconds,updated_at,version)
                       VALUES(?,?,?,?,?,?,?,?,1)
                       ON CONFLICT(key,scope,workspace_id,session_id,agent_id)
                       DO UPDATE SET
                         value=excluded.value,
                         updated_at=excluded.updated_at,
                         version=version+1""",
                    (row["key"], row["value"], "session", row["workspace_id"],
                     dst_session_id, row["agent_id"], row["ttl_seconds"], now),
                )
                count += 1
            await db.commit()
        return count


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

class ConflictError(Exception):
    pass


def _row_to_chunk(row: aiosqlite.Row) -> Chunk:
    range_obj = None
    if row["source_range"]:
        range_obj = SourceRange(**json.loads(row["source_range"]))
    return Chunk(
        id=row["chunk_id"],
        workspace_id=row["workspace_id"],
        source=ChunkSource(
            uri=row["source_uri"],
            range=range_obj,
            content_hash=row["content_hash"],
        ),
        kind=row["kind"],
        language=row["language"],
        symbol=row["symbol"],
        content=row["content"],
        metadata=json.loads(row["metadata"]),
        version=row["version"],
    )


def _row_to_state(row: aiosqlite.Row) -> StateEntry:
    return StateEntry(
        key=row["key"],
        value=json.loads(row["value"]),
        scope=Scope(row["scope"]),
        workspace_id=row["workspace_id"],
        session_id=row["session_id"],
        agent_id=row["agent_id"],
        ttl_seconds=row["ttl_seconds"],
        updated_at=row["updated_at"],
        version=row["version"],
    )
