"""OCP reference server — MCP wiring."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    LoggingMessageNotification,
    LoggingMessageNotificationParams,
    TextContent,
    Tool,
)

from ocp_server.auth import (
    PermissionDeniedError,
    get_auth_context,
    load_auth_config,
    set_auth_context,
)
from ocp_server.embedder import make_embedder, Tokenizer
from ocp_server.indexer import index_workspace
from ocp_server.storage.sqlite import SQLiteStore
from ocp_server.tools import coordination, events, retrieval, state, workspace

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Error helper                                                         #
# ------------------------------------------------------------------ #

def _ocp_error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _handle_known(exc: Exception) -> dict | None:
    code = getattr(exc, "code", None)
    if code:
        return _ocp_error(code, str(exc))
    return None


def _check_ws(workspace_id: str) -> None:
    """Raise PermissionDeniedError if current auth context cannot access workspace."""
    get_auth_context().assert_workspace(workspace_id)


# ------------------------------------------------------------------ #
# Notification helper                                                  #
# ------------------------------------------------------------------ #

async def _send_ocp_notification(app: Server, envelope: dict) -> None:
    """Send an OCP event as an MCP log notification (info level).

    Falls back silently if not inside a request context — events are
    at-most-once per §7.3, so loss during teardown is acceptable.
    """
    try:
        ctx = app.request_context
        notification: Any = LoggingMessageNotification(
            method="notifications/message",
            params=LoggingMessageNotificationParams(
                level="info",
                logger="ocp.events",
                data=envelope,
            ),
        )
        await ctx.session.send_notification(notification)
    except LookupError:
        pass  # not inside a request context
    except Exception as exc:
        log.debug("Could not send OCP notification: %s", exc)


# ------------------------------------------------------------------ #
# Server factory                                                       #
# ------------------------------------------------------------------ #

def build_server(db_path: str = "ocp.db") -> tuple[Server, SQLiteStore, Any, Tokenizer, Any]:
    store = SQLiteStore(db_path)
    embedder = make_embedder()
    tokenizer = Tokenizer()

    app = Server(
        name="ocp-server",
        version="0.1.0",
        # §10: core+coordination+events implemented; §8 auth/isolation not implemented
        # so we advertise core+coordination, not full.
        instructions=(
            "OCP/0.1 reference server — profiles: ocp/0.1 — conformance: core+coordination. "
            "context.search: k capped at 20 (§4.2). "
            "Embedding backend: OCP_EMBEDDER=hash|fastembed|openai (default: hash)."
        ),
    )

    # Convenience: emit an OCP event to all subscribers and optionally notify.
    async def _emit(workspace_id: str, event_type: str, payload: dict) -> None:
        async def _notify_cb(subscription_id: str, envelope: dict) -> None:
            await _send_ocp_notification(app, envelope)
        await events.emit_event(store, workspace_id, event_type, payload, _notify_cb)

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return _ALL_TOOLS

    @app.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        import json
        try:
            result = await _dispatch(name, arguments, store, embedder, tokenizer, _emit)
        except PermissionDeniedError as exc:
            result = _ocp_error("PERMISSION_DENIED", str(exc))
        except Exception as exc:
            err = _handle_known(exc)
            if err:
                result = err
            else:
                raise
        return [TextContent(type="text", text=json.dumps(result))]

    return app, store, embedder, tokenizer, _emit


# ------------------------------------------------------------------ #
# Dispatch                                                             #
# ------------------------------------------------------------------ #

async def _dispatch(
    name: str,
    args: dict,
    store: SQLiteStore,
    embedder: Any,
    tokenizer: Tokenizer,
    emit: Any,
) -> Any:
    match name:

        # ── workspace ──────────────────────────────────────────────
        case "workspace.register":
            result = await workspace.workspace_register(
                store,
                root_uri=args["root_uri"],
                name=args.get("name"),
                metadata=args.get("metadata", {}),
            )
            # Grant access check AFTER registration so the key can bootstrap
            # its first workspace. On subsequent calls the workspace already exists.
            _check_ws(result["workspace_id"])
            return result

        case "workspace.index":
            ws_id = args["workspace_id"]
            _check_ws(ws_id)
            root_uri = await store.get_workspace_root(ws_id)
            if root_uri is None:
                return _ocp_error("WORKSPACE_NOT_FOUND", f"Workspace not found: {ws_id}")

            paths = args.get("paths")
            wait = args.get("wait", True)

            async def _do_index(wid: str = ws_id, ruri: str = root_uri) -> None:
                # §7.2 index.progress callback
                async def _progress(frac: float) -> None:
                    await emit(wid, "index.progress",
                               {"workspace_id": wid, "progress": round(frac, 3)})

                result, stale_ids = await index_workspace(
                    store, embedder, wid, ruri, paths, progress_cb=_progress
                )
                # §6.1 trigger 3 — emit chunk.invalidated for hash-mismatched chunks
                if stale_ids:
                    await emit(wid, "chunk.invalidated",
                               {"chunk_ids": stale_ids, "reason": "file_changed"})
                # §7.2 chunk.indexed
                chunks_page, _ = await store.list_chunks(wid, None, None)
                await emit(wid, "chunk.indexed",
                           {"chunk_ids": [c.id for c in chunks_page[:200]]})

            if wait is False:
                # §4.1 — async mode: return immediately, index in background task
                asyncio.create_task(_do_index())
                return {"indexed": 0, "skipped": 0, "duration_ms": 0, "async": True}
            else:
                async def _noop(_: float) -> None: pass
                result, stale_ids = await index_workspace(
                    store, embedder, ws_id, root_uri, paths, progress_cb=_noop
                )
                if stale_ids:
                    await emit(ws_id, "chunk.invalidated",
                               {"chunk_ids": stale_ids, "reason": "file_changed"})
                chunks_page, _ = await store.list_chunks(ws_id, None, None)
                await emit(ws_id, "chunk.indexed",
                           {"chunk_ids": [c.id for c in chunks_page[:200]]})
                return result

        case "workspace.invalidate":
            ws_id = args["workspace_id"]
            _check_ws(ws_id)
            paths = args["paths"]
            # B4: invalidate_chunks_by_path now returns IDs (not a count)
            if not await store.workspace_exists(ws_id):
                return _ocp_error("WORKSPACE_NOT_FOUND", f"Workspace not found: {ws_id}")
            chunk_ids = await store.invalidate_chunks_by_path(ws_id, paths)
            await emit(ws_id, "chunk.invalidated", {
                "chunk_ids": chunk_ids,
                "reason": "explicit",
            })
            return {"invalidated": len(chunk_ids)}

        case "workspace.list_chunks":
            _check_ws(args["workspace_id"])
            return await workspace.workspace_list_chunks(
                store, args["workspace_id"], args.get("filters"), args.get("cursor")
            )

        # ── retrieval ──────────────────────────────────────────────
        case "context.search":
            _check_ws(args["workspace_id"])
            return await retrieval.context_search(
                store, embedder,
                workspace_id=args["workspace_id"],
                query=args["query"],
                k=args.get("k", 5),
                filters=args.get("filters"),
            )

        case "context.get_chunk":
            return await retrieval.context_get_chunk(store, args["chunk_id"])

        case "context.pack":
            _check_ws(args["workspace_id"])
            return await retrieval.context_pack(
                store, embedder, tokenizer,
                workspace_id=args["workspace_id"],
                intent=args["intent"],
                budget_tokens=args["budget_tokens"],
                include_state=args.get("include_state", False),
            )

        # ── state ──────────────────────────────────────────────────
        case "state.set":
            ws_id = args.get("workspace_id")
            if ws_id:
                _check_ws(ws_id)
            sess_id = args.get("session_id")
            agent_id = args.get("agent_id")
            scope = args["scope"]
            result = await state.state_set(
                store,
                key=args["key"],
                value=args["value"],
                scope=scope,
                workspace_id=ws_id,
                session_id=sess_id,
                agent_id=agent_id,
                ttl_seconds=args.get("ttl_seconds"),
                if_version=args.get("if_version"),
            )
            # §7.2 — emit state.changed
            if ws_id:
                await emit(ws_id, "state.changed", {
                    "key": args["key"], "scope": scope,
                    "session_id": sess_id, "agent_id": agent_id,
                    "version": result["version"],
                })
            return result

        case "state.get":
            return await state.state_get(
                store,
                key=args["key"],
                scope=args.get("scope"),
                workspace_id=args.get("workspace_id"),
                session_id=args.get("session_id"),
                agent_id=args.get("agent_id"),
            )

        case "state.list":
            return await state.state_list(
                store,
                prefix=args.get("prefix"),
                scope=args.get("scope"),
                workspace_id=args.get("workspace_id"),
                session_id=args.get("session_id"),
                agent_id=args.get("agent_id"),
                cursor=args.get("cursor"),
            )

        case "state.delete":
            ws_id = args.get("workspace_id")
            if ws_id:
                _check_ws(ws_id)
            scope = args["scope"]
            sess_id = args.get("session_id")
            agent_id = args.get("agent_id")
            result = await state.state_delete(
                store,
                key=args["key"],
                scope=scope,
                workspace_id=ws_id,
                session_id=sess_id,
                agent_id=agent_id,
                if_version=args.get("if_version"),
            )
            if ws_id and result["deleted"]:
                await emit(ws_id, "state.changed", {
                    "key": args["key"], "scope": scope,
                    "session_id": sess_id, "agent_id": agent_id,
                    "version": None, "deleted": True,
                })
            return result

        # ── coordination ───────────────────────────────────────────
        case "session.open":
            _check_ws(args["workspace_id"])
            return await coordination.session_open(
                store,
                workspace_id=args["workspace_id"],
                session_id=args.get("session_id"),
                ttl_seconds=args.get("ttl_seconds"),
                metadata=args.get("metadata", {}),
            )

        case "session.close":
            sess_id = args["session_id"]
            result = await coordination.session_close(store, sess_id)
            # Emit session.closed for all workspaces that have subscribers
            # (session may span any workspace; emit best-effort)
            all_ws = await store.list_all_workspaces()
            for ws in all_ws:
                await emit(ws["workspace_id"], "session.closed",
                           {"session_id": sess_id, "reason": "explicit"})
            return result

        case "session.handoff":
            sess_id = args["session_id"]
            from_agent = args["from_agent"]
            to_agent = args["to_agent"]
            result = await coordination.session_handoff(
                store,
                session_id=sess_id,
                from_agent=from_agent,
                to_agent=to_agent,
                message=args["message"],
            )
            # §7.2 — emit session.handoff event
            all_ws = await store.list_all_workspaces()
            for ws in all_ws:
                await emit(ws["workspace_id"], "session.handoff", {
                    "session_id": sess_id,
                    "from_agent": from_agent,
                    "to_agent": to_agent,
                    "handoff_id": result["handoff_id"],
                })
            return result

        case "session.checkpoint":
            return await coordination.session_checkpoint(
                store,
                session_id=args["session_id"],
                label=args["label"],
                include_state=args.get("include_state", False),
            )

        case "session.restore":
            return await coordination.session_restore(store, args["checkpoint_id"])

        # ── events ─────────────────────────────────────────────────
        case "events.subscribe":
            _check_ws(args["workspace_id"])
            return await events.events_subscribe(
                store,
                workspace_id=args["workspace_id"],
                types=args.get("types"),
                session_id=args.get("session_id"),
                since=args.get("since"),
            )

        case "events.unsubscribe":
            return await events.events_unsubscribe(store, args["subscription_id"])

        case _:
            return _ocp_error("METHOD_NOT_FOUND", f"Unknown tool: {name}")


# ------------------------------------------------------------------ #
# Background tasks                                                     #
# ------------------------------------------------------------------ #

async def _ttl_cleanup_loop(store: SQLiteStore, emit: Any) -> None:
    """§5.2 — Purge expired state entries and sessions every 60 s.
    §7.2 — Emits session.closed with reason=ttl for each expired session.
    """
    while True:
        await asyncio.sleep(60)
        try:
            expired_sessions = await store.purge_expired_sessions_with_ids()
            for sid, ws_id in expired_sessions:
                await store.delete_session_state(sid)
                if ws_id:
                    await emit(ws_id, "session.closed",
                               {"session_id": sid, "reason": "ttl"})
            purged_entries = await store.purge_expired_state_entries()
            total = len(expired_sessions) + purged_entries
            if total:
                log.info("TTL cleanup: %d sessions, %d entries purged",
                         len(expired_sessions), purged_entries)
        except Exception as exc:
            log.warning("TTL cleanup error: %s", exc)


async def _file_watch_loop(store: SQLiteStore, embedder: Any, emit: Any) -> None:
    """§6.1 — Watch all registered workspace roots for file changes.

    B3: emits chunk.invalidated (with actual chunk IDs) on every file change.
    B8: re-queries workspace list every 60 s to pick up newly registered roots.
    """
    try:
        from watchfiles import awatch
    except ImportError:
        log.warning("watchfiles not available — file watching disabled")
        return

    while True:
        workspaces = await store.list_all_workspaces()
        if not workspaces:
            await asyncio.sleep(30)
            continue

        roots = [ws["root_uri"].removeprefix("file://") for ws in workspaces]
        ws_by_root = {ws["root_uri"].removeprefix("file://"): ws["workspace_id"]
                      for ws in workspaces}

        log.info("File watcher (re)started for %d workspace(s): %s", len(roots), roots)
        stop = asyncio.Event()

        # Re-arm every 60 s so newly registered workspaces get picked up (B8)
        async def _rearm():
            await asyncio.sleep(60)
            stop.set()

        rearm_task = asyncio.create_task(_rearm())
        try:
            async for changes in awatch(*roots, stop_event=stop):
                for change_type, path in changes:
                    for root, ws_id in ws_by_root.items():
                        if path.startswith(root):
                            # B3 + B4: get real chunk IDs and emit event
                            chunk_ids = await store.invalidate_chunks_by_path(ws_id, [path])
                            if chunk_ids:
                                log.info(
                                    "Auto-invalidated %d chunk(s) for %s", len(chunk_ids), path
                                )
                                await emit(ws_id, "chunk.invalidated", {
                                    "chunk_ids": chunk_ids,
                                    "reason": "file_changed",
                                })
                            break
        except Exception as exc:
            log.warning("File watcher error: %s — restarting", exc)
        finally:
            rearm_task.cancel()
            await asyncio.gather(rearm_task, return_exceptions=True)


# ------------------------------------------------------------------ #
# Tool schemas                                                         #
# ------------------------------------------------------------------ #

_ALL_TOOLS: list[Tool] = [
    # workspace
    Tool(name="workspace.register",
         description="Register (or return) a workspace root. §4.1",
         inputSchema={"type": "object", "required": ["root_uri"],
                      "properties": {"root_uri": {"type": "string"},
                                     "name": {"type": "string"},
                                     "metadata": {"type": "object"}}}),
    Tool(name="workspace.index",
         description="Trigger indexing of a workspace. §4.1",
         inputSchema={"type": "object", "required": ["workspace_id"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "paths": {"type": "array", "items": {"type": "string"}},
                                     "wait": {"type": "boolean",
                                              "description": "false = return immediately, index in background"}}}),
    Tool(name="workspace.invalidate",
         description="Mark chunks from paths as stale, emits chunk.invalidated. §4.1 §6.2",
         inputSchema={"type": "object", "required": ["workspace_id", "paths"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "paths": {"type": "array", "items": {"type": "string"}}}}),
    Tool(name="workspace.list_chunks",
         description="List chunks in a workspace (OPTIONAL). §4.1",
         inputSchema={"type": "object", "required": ["workspace_id"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "filters": {"type": "object"},
                                     "cursor": {"type": "string"}}}),
    # retrieval
    Tool(name="context.search",
         description="Semantic search over a workspace. §4.2",
         inputSchema={"type": "object", "required": ["workspace_id", "query"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "query": {"type": "string"},
                                     "k": {"type": "integer", "default": 5},
                                     "filters": {"type": "object"}}}),
    Tool(name="context.get_chunk",
         description="Retrieve a chunk by ID. Returns STALE if invalidated. §4.2",
         inputSchema={"type": "object", "required": ["chunk_id"],
                      "properties": {"chunk_id": {"type": "string"}}}),
    Tool(name="context.pack",
         description="Assemble a context bundle under a token budget. §4.2",
         inputSchema={"type": "object", "required": ["workspace_id", "intent", "budget_tokens"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "intent": {"type": "string"},
                                     "budget_tokens": {"type": "integer"},
                                     "include_state": {"type": "boolean", "default": False}}}),
    # state
    Tool(name="state.set",
         description="Write a state entry; emits state.changed. §4.3",
         inputSchema={"type": "object", "required": ["key", "value", "scope"],
                      "properties": {"key": {"type": "string"}, "value": {},
                                     "scope": {"type": "string", "enum": ["agent", "session", "global"]},
                                     "workspace_id": {"type": "string"},
                                     "session_id": {"type": "string"},
                                     "agent_id": {"type": "string"},
                                     "ttl_seconds": {"type": "integer"},
                                     "if_version": {"type": "integer"}}}),
    Tool(name="state.get",
         description="Read a state entry (scope-resolved agent→session→global). §4.3 §5.1",
         inputSchema={"type": "object", "required": ["key"],
                      "properties": {"key": {"type": "string"},
                                     "scope": {"type": "string", "enum": ["agent", "session", "global"]},
                                     "workspace_id": {"type": "string"},
                                     "session_id": {"type": "string"},
                                     "agent_id": {"type": "string"}}}),
    Tool(name="state.list",
         description="List state entries with optional prefix filter. §4.3",
         inputSchema={"type": "object",
                      "properties": {"prefix": {"type": "string"},
                                     "scope": {"type": "string"},
                                     "workspace_id": {"type": "string"},
                                     "session_id": {"type": "string"},
                                     "agent_id": {"type": "string"},
                                     "cursor": {"type": "string"}}}),
    Tool(name="state.delete",
         description="Delete a state entry; emits state.changed. §4.3",
         inputSchema={"type": "object", "required": ["key", "scope"],
                      "properties": {"key": {"type": "string"},
                                     "scope": {"type": "string", "enum": ["agent", "session", "global"]},
                                     "workspace_id": {"type": "string"},
                                     "session_id": {"type": "string"},
                                     "agent_id": {"type": "string"},
                                     "if_version": {"type": "integer"}}}),
    # coordination
    Tool(name="session.open",
         description="Open or materialise a session. §4.4",
         inputSchema={"type": "object", "required": ["workspace_id"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "session_id": {"type": "string"},
                                     "ttl_seconds": {"type": "integer"},
                                     "metadata": {"type": "object"}}}),
    Tool(name="session.close",
         description="Close a session; emits session.closed. §4.4",
         inputSchema={"type": "object", "required": ["session_id"],
                      "properties": {"session_id": {"type": "string"}}}),
    Tool(name="session.handoff",
         description="Pass control between agents; emits session.handoff. §4.4",
         inputSchema={"type": "object",
                      "required": ["session_id", "from_agent", "to_agent", "message"],
                      "properties": {"session_id": {"type": "string"},
                                     "from_agent": {"type": "string"},
                                     "to_agent": {"type": "string"},
                                     "message": {}}}),
    Tool(name="session.checkpoint",
         description="Create a named session checkpoint. §4.4",
         inputSchema={"type": "object", "required": ["session_id", "label"],
                      "properties": {"session_id": {"type": "string"},
                                     "label": {"type": "string"},
                                     "include_state": {"type": "boolean"}}}),
    Tool(name="session.restore",
         description="Restore a session from a checkpoint, copying state. §4.4",
         inputSchema={"type": "object", "required": ["checkpoint_id"],
                      "properties": {"checkpoint_id": {"type": "string"}}}),
    # events
    Tool(name="events.subscribe",
         description="Subscribe to workspace events; supports replay via 'since'. §4.5 §7",
         inputSchema={"type": "object", "required": ["workspace_id"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "types": {"type": "array", "items": {"type": "string"}},
                                     "session_id": {"type": "string"},
                                     "since": {"type": "string"}}}),
    Tool(name="events.unsubscribe",
         description="Unsubscribe from events. §4.5",
         inputSchema={"type": "object", "required": ["subscription_id"],
                      "properties": {"subscription_id": {"type": "string"}}}),
]


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

async def _main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db_path = os.environ.get("OCP_DB_PATH", "ocp.db")
    watch = os.environ.get("OCP_WATCH", "1") != "0"

    # Auth: in stdio mode the spawning process sets OCP_API_KEY.
    # If unset the server runs in open/dev mode.
    auth_cfg = load_auth_config()
    api_key = os.environ.get("OCP_API_KEY", "").strip()
    if auth_cfg.enabled:
        ctx = auth_cfg.validate_key(api_key)
        if ctx is None:
            raise SystemExit(
                f"OCP_API_KEY='{api_key}' is not in OCP_API_KEYS. Refusing to start."
            )
        set_auth_context(ctx)
        log.info("Auth enabled — key grants access to %s",
                 "all workspaces" if ctx.allowed_workspaces is None
                 else str(ctx.allowed_workspaces))
    else:
        log.warning(
            "OCP_API_KEYS not set — running in open/dev mode. "
            "Do not expose to untrusted clients."
        )

    app, store, embedder, tokenizer, emit_fn = build_server(db_path)
    await store.setup()

    # Start background tasks
    ttl_task = asyncio.create_task(_ttl_cleanup_loop(store, emit_fn))
    watch_task = asyncio.create_task(_file_watch_loop(store, embedder, emit_fn)) if watch else None

    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        ttl_task.cancel()
        if watch_task:
            watch_task.cancel()
        await asyncio.gather(ttl_task, watch_task or asyncio.sleep(0),
                             return_exceptions=True)


def main() -> None:
    try:
        asyncio.run(_main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
