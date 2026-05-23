"""PostgreSQL-backed OCP store using asyncpg + pgvector.

Requirements
------------
- PostgreSQL 14+ with the pgvector extension:
    CREATE EXTENSION IF NOT EXISTS vector;
- Set OCP_DATABASE_URL=postgresql://user:pass@host:5432/dbname
- The schema is created automatically on first run via setup().

Connection pooling
------------------
asyncpg.Pool is used. Pool size is configurable via:
    OCP_DB_POOL_MIN  (default 2)
    OCP_DB_POOL_MAX  (default 10)

Vector search
-------------
pgvector's <=> (cosine distance) operator is used. Embeddings are stored as
the native vector type. An IVFFlat index is created after the first 1000 chunks
are indexed (configurable via OCP_PGVECTOR_LISTS).
"""
from __future__ import annotations

import datetime
import json
import os
import time
import uuid
from typing import Any

import asyncpg

from ocp_server.models import Chunk, ChunkSource, Scope, SourceRange, StateEntry
from ocp_server.storage.base import BaseStore
from ocp_server.storage.sqlite import ConflictError   # reuse the same exception class

_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS ocp_workspaces (
    workspace_id TEXT PRIMARY KEY,
    root_uri     TEXT NOT NULL,
    name         TEXT,
    metadata     JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS ocp_chunks (
    chunk_id     TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES ocp_workspaces(workspace_id),
    source_uri   TEXT NOT NULL,
    source_range JSONB,
    content_hash TEXT NOT NULL,
    kind         TEXT NOT NULL,
    language     TEXT,
    symbol       TEXT,
    content      TEXT NOT NULL,
    metadata     JSONB NOT NULL DEFAULT '{}',
    version      INTEGER NOT NULL DEFAULT 1,
    stale        BOOLEAN NOT NULL DEFAULT FALSE,
    embedding    vector
);

CREATE INDEX IF NOT EXISTS idx_ocp_chunks_workspace
    ON ocp_chunks(workspace_id);
CREATE INDEX IF NOT EXISTS idx_ocp_chunks_uri
    ON ocp_chunks(workspace_id, source_uri);

CREATE TABLE IF NOT EXISTS ocp_state (
    id           BIGSERIAL PRIMARY KEY,
    key          TEXT NOT NULL,
    value        JSONB NOT NULL,
    scope        TEXT NOT NULL,
    workspace_id TEXT,
    session_id   TEXT,
    agent_id     TEXT,
    ttl_seconds  INTEGER,
    updated_at   TIMESTAMPTZ NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    UNIQUE (key, scope, workspace_id, session_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_ocp_state_lookup
    ON ocp_state(scope, workspace_id, session_id, agent_id, key);

CREATE TABLE IF NOT EXISTS ocp_sessions (
    session_id   TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    ttl_seconds  INTEGER,
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL,
    closed       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS ocp_subscriptions (
    subscription_id TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL,
    session_id      TEXT,
    types           TEXT[],
    created_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ocp_events (
    event_id        TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL REFERENCES ocp_subscriptions(subscription_id)
                        ON DELETE CASCADE,
    workspace_id    TEXT NOT NULL,
    type            TEXT NOT NULL,
    payload         JSONB NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ocp_events_sub
    ON ocp_events(subscription_id, event_id);
CREATE INDEX IF NOT EXISTS idx_ocp_events_ws
    ON ocp_events(workspace_id, event_id);
"""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _event_id() -> str:
    return f"evt_{time.time_ns():020d}_{uuid.uuid4().hex[:6]}"


class PostgresStore(BaseStore):
    """Production-grade OCP store backed by PostgreSQL + pgvector."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.environ.get(
            "OCP_DATABASE_URL", "postgresql://localhost/ocp"
        )
        self._pool: asyncpg.Pool | None = None
        self._pool_min = int(os.environ.get("OCP_DB_POOL_MIN", "2"))
        self._pool_max = int(os.environ.get("OCP_DB_POOL_MAX", "10"))
        self._embed_dim: int | None = None

    async def setup(self) -> None:
        async def _init(conn: asyncpg.Connection) -> None:
            await conn.execute("SET search_path TO public")
            await conn.set_type_codec(
                "jsonb",
                encoder=json.dumps,
                decoder=json.loads,
                schema="pg_catalog",
            )

        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=self._pool_min,
            max_size=self._pool_max,
            init=_init,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA)

    def _pool_conn(self) -> asyncpg.pool.PoolConnectionProxy:
        if self._pool is None:
            raise RuntimeError("PostgresStore.setup() has not been called")
        return self._pool.acquire()

    # ------------------------------------------------------------------ #
    # Workspace                                                            #
    # ------------------------------------------------------------------ #

    async def workspace_exists(self, workspace_id: str) -> bool:
        async with self._pool_conn() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM ocp_workspaces WHERE workspace_id=$1", workspace_id
            )
        return row is not None

    async def create_workspace(
        self, workspace_id: str, root_uri: str, name: str | None, metadata: dict
    ) -> None:
        async with self._pool_conn() as conn:
            await conn.execute(
                """INSERT INTO ocp_workspaces(workspace_id,root_uri,name,metadata)
                   VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING""",
                workspace_id, root_uri, name, metadata,
            )

    async def get_workspace_root(self, workspace_id: str) -> str | None:
        async with self._pool_conn() as conn:
            row = await conn.fetchrow(
                "SELECT root_uri FROM ocp_workspaces WHERE workspace_id=$1",
                workspace_id,
            )
        return row["root_uri"] if row else None

    async def list_all_workspaces(self) -> list[dict]:
        async with self._pool_conn() as conn:
            rows = await conn.fetch("SELECT * FROM ocp_workspaces")
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Chunks                                                               #
    # ------------------------------------------------------------------ #

    async def upsert_chunk(self, chunk: Chunk, embedding: list[float]) -> None:
        range_j = (
            json.dumps(chunk.source.range.model_dump()) if chunk.source.range else None
        )
        # Register embedding dimension on first upsert, create IVFFlat index later
        if self._embed_dim is None:
            self._embed_dim = len(embedding)

        vec_str = f"[{','.join(str(x) for x in embedding)}]"
        async with self._pool_conn() as conn:
            await conn.execute(
                """INSERT INTO ocp_chunks
                   (chunk_id,workspace_id,source_uri,source_range,content_hash,
                    kind,language,symbol,content,metadata,version,stale,embedding)
                   VALUES($1,$2,$3,$4::jsonb,$5,$6,$7,$8,$9,$10::jsonb,1,FALSE,$11::vector)
                   ON CONFLICT(chunk_id) DO UPDATE SET
                     content        = EXCLUDED.content,
                     content_hash   = EXCLUDED.content_hash,
                     version        = ocp_chunks.version + 1,
                     stale          = FALSE,
                     embedding      = EXCLUDED.embedding,
                     metadata       = EXCLUDED.metadata""",
                chunk.id, chunk.workspace_id, chunk.source.uri,
                range_j, chunk.source.content_hash,
                chunk.kind, chunk.language, chunk.symbol,
                chunk.content, json.dumps(chunk.metadata), vec_str,
            )

    async def get_chunk(self, chunk_id: str) -> Chunk | None:
        async with self._pool_conn() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM ocp_chunks WHERE chunk_id=$1", chunk_id
            )
        return _row_to_chunk(row) if row else None

    async def is_chunk_stale(self, chunk_id: str) -> bool:
        async with self._pool_conn() as conn:
            row = await conn.fetchrow(
                "SELECT stale FROM ocp_chunks WHERE chunk_id=$1", chunk_id
            )
        return bool(row["stale"]) if row else False

    async def invalidate_chunks_by_path(
        self, workspace_id: str, paths: list[str]
    ) -> list[str]:
        from pathlib import Path as _Path
        chunk_ids: list[str] = []
        async with self._pool_conn() as conn:
            for path in paths:
                raw = path.removeprefix("file://") if path.startswith("file://") else path
                try:
                    resolved = str(_Path(raw).resolve())
                except Exception:
                    resolved = raw
                pattern = f"file://{resolved}%"
                rows = await conn.fetch(
                    """UPDATE ocp_chunks SET stale=TRUE
                       WHERE workspace_id=$1 AND source_uri LIKE $2 AND stale=FALSE
                       RETURNING chunk_id""",
                    workspace_id, pattern,
                )
                chunk_ids.extend(r["chunk_id"] for r in rows)
        return chunk_ids

    async def search_chunks(
        self,
        workspace_id: str,
        query_embedding: list[float],
        k: int,
        filters: dict[str, Any] | None,
    ) -> list[tuple[Chunk, float]]:
        vec_str = f"[{','.join(str(x) for x in query_embedding)}]"
        where = "workspace_id=$1 AND stale=FALSE AND embedding IS NOT NULL"
        params: list[Any] = [workspace_id]
        i = 2
        if filters:
            if "kind" in filters:
                where += f" AND kind=${i}"
                params.append(filters["kind"])
                i += 1
            if "language" in filters:
                where += f" AND language=${i}"
                params.append(filters["language"])
                i += 1

        params.append(vec_str)
        params.append(k)
        async with self._pool_conn() as conn:
            rows = await conn.fetch(
                f"""SELECT *, 1 - (embedding <=> ${i}::vector) AS score
                    FROM ocp_chunks
                    WHERE {where}
                    ORDER BY embedding <=> ${i}::vector
                    LIMIT ${i+1}""",
                *params,
            )
        return [(_row_to_chunk(r), float(r["score"])) for r in rows]

    async def list_chunks(
        self, workspace_id: str, filters: dict | None, cursor: str | None
    ) -> tuple[list[Chunk], str | None]:
        offset = int(cursor) if cursor else 0
        page = 50
        async with self._pool_conn() as conn:
            rows = await conn.fetch(
                "SELECT * FROM ocp_chunks WHERE workspace_id=$1 LIMIT $2 OFFSET $3",
                workspace_id, page + 1, offset,
            )
        next_cursor = str(offset + page) if len(rows) > page else None
        return [_row_to_chunk(r) for r in rows[:page]], next_cursor

    async def get_active_chunk_ids_for_uri(
        self, workspace_id: str, uri: str
    ) -> list[str]:
        async with self._pool_conn() as conn:
            rows = await conn.fetch(
                "SELECT chunk_id FROM ocp_chunks WHERE workspace_id=$1 AND source_uri=$2 AND stale=FALSE",
                workspace_id, uri,
            )
        return [r["chunk_id"] for r in rows]

    async def mark_chunks_stale(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        async with self._pool_conn() as conn:
            await conn.execute(
                "UPDATE ocp_chunks SET stale=TRUE WHERE chunk_id=ANY($1::text[])",
                chunk_ids,
            )

    # ------------------------------------------------------------------ #
    # State                                                                #
    # ------------------------------------------------------------------ #

    async def state_set(
        self, entry: StateEntry, if_version: int | None = None
    ) -> int:
        async with self._pool_conn() as conn:
            async with conn.transaction():
                if if_version is not None:
                    row = await conn.fetchrow(
                        """SELECT version FROM ocp_state
                           WHERE key=$1 AND scope=$2
                             AND workspace_id IS NOT DISTINCT FROM $3
                             AND session_id IS NOT DISTINCT FROM $4
                             AND agent_id IS NOT DISTINCT FROM $5""",
                        entry.key, entry.scope.value,
                        entry.workspace_id, entry.session_id, entry.agent_id,
                    )
                    current = row["version"] if row else 0
                    if current != if_version:
                        raise ConflictError(
                            f"version mismatch: expected {if_version}, got {current}"
                        )

                # S3: lazy session materialisation
                if entry.scope == Scope.session and entry.session_id:
                    await conn.execute(
                        """INSERT INTO ocp_sessions
                           (session_id,workspace_id,ttl_seconds,metadata,created_at)
                           VALUES($1,$2,NULL,'{}',$3)
                           ON CONFLICT DO NOTHING""",
                        entry.session_id, entry.workspace_id or "", _now(),
                    )

                row = await conn.fetchrow(
                    """INSERT INTO ocp_state
                       (key,value,scope,workspace_id,session_id,agent_id,
                        ttl_seconds,updated_at,version)
                       VALUES($1,$2::jsonb,$3,$4,$5,$6,$7,$8,1)
                       ON CONFLICT(key,scope,workspace_id,session_id,agent_id)
                       DO UPDATE SET
                         value=EXCLUDED.value,
                         ttl_seconds=EXCLUDED.ttl_seconds,
                         updated_at=EXCLUDED.updated_at,
                         version=ocp_state.version+1
                       RETURNING version""",
                    entry.key, json.dumps(entry.value), entry.scope.value,
                    entry.workspace_id, entry.session_id, entry.agent_id,
                    entry.ttl_seconds, _now(),
                )
        return row["version"]

    async def state_get(
        self, key: str, scope: Scope,
        workspace_id: str | None, session_id: str | None, agent_id: str | None,
    ) -> StateEntry | None:
        async with self._pool_conn() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM ocp_state
                   WHERE key=$1 AND scope=$2
                     AND workspace_id IS NOT DISTINCT FROM $3
                     AND session_id IS NOT DISTINCT FROM $4
                     AND agent_id IS NOT DISTINCT FROM $5""",
                key, scope.value, workspace_id, session_id, agent_id,
            )
        return _row_to_state(row) if row else None

    async def state_list(
        self,
        prefix: str | None, scope: Scope | None,
        workspace_id: str | None, session_id: str | None, agent_id: str | None,
        cursor: str | None,
    ) -> tuple[list[StateEntry], str | None]:
        offset = int(cursor) if cursor else 0
        page = 100
        conditions = []
        params: list[Any] = []
        i = 1
        if prefix:
            conditions.append(f"key LIKE ${i}")
            params.append(f"{prefix}%")
            i += 1
        if scope:
            conditions.append(f"scope=${i}")
            params.append(scope.value)
            i += 1
        if workspace_id is not None:
            conditions.append(f"workspace_id=${i}")
            params.append(workspace_id)
            i += 1
        if session_id is not None:
            conditions.append(f"session_id=${i}")
            params.append(session_id)
            i += 1
        if agent_id is not None:
            conditions.append(f"agent_id=${i}")
            params.append(agent_id)
            i += 1
        where = " AND ".join(conditions) if conditions else "TRUE"
        params += [page + 1, offset]
        async with self._pool_conn() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM ocp_state WHERE {where} LIMIT ${i} OFFSET ${i+1}",
                *params,
            )
        next_cursor = str(offset + page) if len(rows) > page else None
        return [_row_to_state(r) for r in rows[:page]], next_cursor

    async def state_delete(
        self, key: str, scope: Scope,
        workspace_id: str | None, session_id: str | None, agent_id: str | None,
        if_version: int | None,
    ) -> bool:
        async with self._pool_conn() as conn:
            async with conn.transaction():
                if if_version is not None:
                    row = await conn.fetchrow(
                        """SELECT version FROM ocp_state
                           WHERE key=$1 AND scope=$2
                             AND workspace_id IS NOT DISTINCT FROM $3
                             AND session_id IS NOT DISTINCT FROM $4
                             AND agent_id IS NOT DISTINCT FROM $5""",
                        key, scope.value, workspace_id, session_id, agent_id,
                    )
                    if row is None:
                        return False
                    if row["version"] != if_version:
                        raise ConflictError(
                            f"delete version mismatch: expected {if_version}, "
                            f"got {row['version']}"
                        )
                row = await conn.fetchrow(
                    """DELETE FROM ocp_state
                       WHERE key=$1 AND scope=$2
                         AND workspace_id IS NOT DISTINCT FROM $3
                         AND session_id IS NOT DISTINCT FROM $4
                         AND agent_id IS NOT DISTINCT FROM $5
                       RETURNING 1""",
                    key, scope.value, workspace_id, session_id, agent_id,
                )
        return row is not None

    # ------------------------------------------------------------------ #
    # Sessions                                                             #
    # ------------------------------------------------------------------ #

    async def session_open(
        self, workspace_id: str, session_id: str,
        ttl_seconds: int | None, metadata: dict,
    ) -> None:
        async with self._pool_conn() as conn:
            await conn.execute(
                """INSERT INTO ocp_sessions
                   (session_id,workspace_id,ttl_seconds,metadata,created_at)
                   VALUES($1,$2,$3,$4::jsonb,$5) ON CONFLICT DO NOTHING""",
                session_id, workspace_id, ttl_seconds, metadata, _now(),
            )

    async def session_close(self, session_id: str) -> bool:
        async with self._pool_conn() as conn:
            row = await conn.fetchrow(
                """UPDATE ocp_sessions SET closed=TRUE
                   WHERE session_id=$1 AND closed=FALSE RETURNING 1""",
                session_id,
            )
        return row is not None

    async def session_exists(self, session_id: str) -> bool:
        async with self._pool_conn() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM ocp_sessions WHERE session_id=$1 AND closed=FALSE",
                session_id,
            )
        return row is not None

    async def get_session_workspace(self, session_id: str) -> str | None:
        async with self._pool_conn() as conn:
            row = await conn.fetchrow(
                "SELECT workspace_id FROM ocp_sessions WHERE session_id=$1",
                session_id,
            )
        return row["workspace_id"] if row else None

    async def delete_session_state(self, session_id: str) -> int:
        async with self._pool_conn() as conn:
            result = await conn.execute(
                "DELETE FROM ocp_state WHERE session_id=$1", session_id
            )
        return int(result.split()[-1])

    # ------------------------------------------------------------------ #
    # Events / subscriptions                                               #
    # ------------------------------------------------------------------ #

    async def create_subscription(
        self, workspace_id: str, types: list[str] | None, session_id: str | None
    ) -> str:
        sub_id = f"sub_{uuid.uuid4().hex[:12]}"
        async with self._pool_conn() as conn:
            await conn.execute(
                """INSERT INTO ocp_subscriptions
                   (subscription_id,workspace_id,session_id,types,created_at)
                   VALUES($1,$2,$3,$4,$5)""",
                sub_id, workspace_id, session_id, types, _now(),
            )
        return sub_id

    async def delete_subscription(self, subscription_id: str) -> bool:
        async with self._pool_conn() as conn:
            row = await conn.fetchrow(
                "DELETE FROM ocp_subscriptions WHERE subscription_id=$1 RETURNING 1",
                subscription_id,
            )
        return row is not None

    async def get_subscriptions_for_workspace(self, workspace_id: str) -> list[dict]:
        async with self._pool_conn() as conn:
            rows = await conn.fetch(
                "SELECT * FROM ocp_subscriptions WHERE workspace_id=$1", workspace_id
            )
        result = []
        for r in rows:
            d = dict(r)
            # Normalise types to match sqlite's JSON-string format consumed by emit_event
            if d.get("types") is not None:
                d["types"] = json.dumps(d["types"])
            result.append(d)
        return result

    async def append_event(
        self, workspace_id: str, subscription_id: str, event_type: str, payload: dict
    ) -> str:
        event_id = _event_id()
        async with self._pool_conn() as conn:
            await conn.execute(
                """INSERT INTO ocp_events
                   (event_id,subscription_id,workspace_id,type,payload,timestamp)
                   VALUES($1,$2,$3,$4,$5::jsonb,$6)""",
                event_id, subscription_id, workspace_id, event_type, payload, _now(),
            )
        return event_id

    async def list_events(self, subscription_id: str, since: str | None) -> list[dict]:
        async with self._pool_conn() as conn:
            if since:
                rows = await conn.fetch(
                    """SELECT * FROM ocp_events
                       WHERE subscription_id=$1 AND event_id>$2
                       ORDER BY event_id LIMIT 1000""",
                    subscription_id, since,
                )
            else:
                rows = await conn.fetch(
                    """SELECT * FROM ocp_events WHERE subscription_id=$1
                       ORDER BY event_id LIMIT 1000""",
                    subscription_id,
                )
        return [_pg_event_to_dict(r) for r in rows]

    async def list_events_for_workspace(
        self, workspace_id: str, since: str
    ) -> list[dict]:
        async with self._pool_conn() as conn:
            rows = await conn.fetch(
                """SELECT * FROM ocp_events
                   WHERE workspace_id=$1 AND event_id>$2
                   ORDER BY event_id LIMIT 1000""",
                workspace_id, since,
            )
        return [_pg_event_to_dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # TTL / maintenance                                                    #
    # ------------------------------------------------------------------ #

    async def purge_expired_state(self) -> int:
        entries = await self.purge_expired_state_entries()
        sessions = await self.purge_expired_sessions_with_ids()
        return entries + len(sessions)

    async def purge_expired_state_entries(self) -> int:
        async with self._pool_conn() as conn:
            result = await conn.execute(
                """DELETE FROM ocp_state
                   WHERE ttl_seconds IS NOT NULL
                     AND EXTRACT(EPOCH FROM (NOW() - updated_at)) > ttl_seconds"""
            )
        return int(result.split()[-1])

    async def purge_expired_sessions_with_ids(self) -> list[tuple[str, str]]:
        async with self._pool_conn() as conn:
            rows = await conn.fetch(
                """UPDATE ocp_sessions SET closed=TRUE
                   WHERE ttl_seconds IS NOT NULL AND closed=FALSE
                     AND EXTRACT(EPOCH FROM (NOW() - created_at)) > ttl_seconds
                   RETURNING session_id, workspace_id"""
            )
        return [(r["session_id"], r["workspace_id"]) for r in rows]

    # ------------------------------------------------------------------ #
    # Checkpoint / restore                                                 #
    # ------------------------------------------------------------------ #

    async def get_checkpoint(self, checkpoint_id: str) -> dict | None:
        async with self._pool_conn() as conn:
            row = await conn.fetchrow(
                "SELECT value, session_id FROM ocp_state WHERE key=$1",
                f"_checkpoint.{checkpoint_id}",
            )
        if row is None:
            return None
        return {
            "checkpoint_id": checkpoint_id,
            **row["value"],
            "session_id": row["session_id"],
        }

    async def copy_session_state(
        self, src_session_id: str, dst_session_id: str
    ) -> int:
        async with self._pool_conn() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """SELECT * FROM ocp_state
                       WHERE scope='session' AND session_id=$1""",
                    src_session_id,
                )
                count = 0
                for row in rows:
                    if row["key"].startswith("_checkpoint."):
                        continue
                    await conn.execute(
                        """INSERT INTO ocp_state
                           (key,value,scope,workspace_id,session_id,agent_id,
                            ttl_seconds,updated_at,version)
                           VALUES($1,$2::jsonb,'session',$3,$4,$5,$6,$7,1)
                           ON CONFLICT(key,scope,workspace_id,session_id,agent_id)
                           DO UPDATE SET
                             value=EXCLUDED.value,
                             updated_at=EXCLUDED.updated_at,
                             version=ocp_state.version+1""",
                        row["key"], json.dumps(row["value"]),
                        row["workspace_id"], dst_session_id,
                        row["agent_id"], row["ttl_seconds"], _now(),
                    )
                    count += 1
        return count

    # ------------------------------------------------------------------ #
    # Prompt traces (not yet implemented for Postgres)                     #
    # ------------------------------------------------------------------ #

    async def save_prompt_trace(self, _trace: dict) -> None:
        raise NotImplementedError("prompt_traces not yet implemented for PostgresStore")

    async def record_prompt_result(self, _trace_id: str, _result: str) -> bool:
        raise NotImplementedError("prompt_traces not yet implemented for PostgresStore")

    async def get_prompt_trace(self, _trace_id: str) -> dict | None:
        raise NotImplementedError("prompt_traces not yet implemented for PostgresStore")

    async def get_trace_stats(
        self, _workspace_id: str | None = None, _since: str | None = None
    ) -> dict:
        raise NotImplementedError("prompt_traces not yet implemented for PostgresStore")

    async def export_traces(
        self,
        _fmt: str = "alpaca",
        _workspace_id: str | None = None,
        _since: str | None = None,
        _only_completed: bool = True,
    ) -> list[dict]:
        raise NotImplementedError("prompt_traces not yet implemented for PostgresStore")


# ------------------------------------------------------------------ #
# Row helpers                                                          #
# ------------------------------------------------------------------ #

def _row_to_chunk(row: asyncpg.Record) -> Chunk:
    range_obj = None
    if row["source_range"]:
        rdata = row["source_range"]
        if isinstance(rdata, str):
            rdata = json.loads(rdata)
        range_obj = SourceRange(**rdata)
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
        metadata=row["metadata"] if isinstance(row["metadata"], dict)
                 else json.loads(row["metadata"]),
        version=row["version"],
    )


def _row_to_state(row: asyncpg.Record) -> StateEntry:
    value = row["value"]
    if isinstance(value, str):
        value = json.loads(value)
    updated = row["updated_at"]
    updated_str = updated.isoformat().replace("+00:00", "Z") if updated else None
    return StateEntry(
        key=row["key"],
        value=value,
        scope=Scope(row["scope"]),
        workspace_id=row["workspace_id"],
        session_id=row["session_id"],
        agent_id=row["agent_id"],
        ttl_seconds=row["ttl_seconds"],
        updated_at=updated_str,
        version=row["version"],
    )


def _pg_event_to_dict(row: asyncpg.Record) -> dict:
    d = dict(row)
    if isinstance(d.get("payload"), str):
        d["payload"] = json.loads(d["payload"])
    if isinstance(d.get("timestamp"), datetime.datetime):
        d["timestamp"] = d["timestamp"].isoformat().replace("+00:00", "Z")
    return d
