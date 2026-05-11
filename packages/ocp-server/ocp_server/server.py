"""OCP reference server — MCP wiring."""
from __future__ import annotations

import asyncio
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ocp_server.embedder import Embedder, Tokenizer
from ocp_server.indexer import index_workspace
from ocp_server.storage.sqlite import SQLiteStore
from ocp_server.tools import coordination, events, retrieval, state, workspace

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


# ------------------------------------------------------------------ #
# Server factory                                                       #
# ------------------------------------------------------------------ #

def build_server(db_path: str = "ocp.db") -> tuple[Server, SQLiteStore, Embedder, Tokenizer]:
    store = SQLiteStore(db_path)
    embedder = Embedder()
    tokenizer = Tokenizer()

    app = Server("ocp-server")

    # MCP server descriptor
    @app.get_server_info()  # type: ignore[attr-defined]
    async def server_info() -> dict:
        return {
            "name": "ocp-server",
            "version": "0.1.0",
            "profiles": ["ocp/0.1"],
            "conformance": "full",
        }

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return _ALL_TOOLS

    @app.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        import json
        try:
            result = await _dispatch(name, arguments, store, embedder, tokenizer)
        except Exception as exc:
            err = _handle_known(exc)
            if err:
                result = err
            else:
                raise
        return [TextContent(type="text", text=json.dumps(result))]

    return app, store, embedder, tokenizer


async def _dispatch(
    name: str,
    args: dict,
    store: SQLiteStore,
    embedder: Embedder,
    tokenizer: Tokenizer,
) -> Any:
    match name:
        # --- workspace ---
        case "workspace.register":
            return await workspace.workspace_register(
                store,
                root_uri=args["root_uri"],
                name=args.get("name"),
                metadata=args.get("metadata", {}),
            )
        case "workspace.index":
            ws_id = args["workspace_id"]
            ws_row = await store._conn()
            async with ws_row.execute("SELECT root_uri FROM workspaces WHERE workspace_id=?", (ws_id,)) as cur:
                row = await cur.fetchone()
            if row is None:
                return _ocp_error("WORKSPACE_NOT_FOUND", f"Workspace not found: {ws_id}")
            result = await index_workspace(
                store, embedder, ws_id, row["root_uri"],
                paths=args.get("paths"),
            )
            return result
        case "workspace.invalidate":
            return await workspace.workspace_invalidate(store, args["workspace_id"], args["paths"])
        case "workspace.list_chunks":
            return await workspace.workspace_list_chunks(
                store, args["workspace_id"], args.get("filters"), args.get("cursor")
            )

        # --- retrieval ---
        case "context.search":
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
            return await retrieval.context_pack(
                store, embedder, tokenizer,
                workspace_id=args["workspace_id"],
                intent=args["intent"],
                budget_tokens=args["budget_tokens"],
                include_state=args.get("include_state", False),
            )

        # --- state ---
        case "state.set":
            return await state.state_set(
                store,
                key=args["key"],
                value=args["value"],
                scope=args["scope"],
                workspace_id=args.get("workspace_id"),
                session_id=args.get("session_id"),
                agent_id=args.get("agent_id"),
                ttl_seconds=args.get("ttl_seconds"),
                if_version=args.get("if_version"),
            )
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
            return await state.state_delete(
                store,
                key=args["key"],
                scope=args["scope"],
                workspace_id=args.get("workspace_id"),
                session_id=args.get("session_id"),
                agent_id=args.get("agent_id"),
                if_version=args.get("if_version"),
            )

        # --- coordination ---
        case "session.open":
            return await coordination.session_open(
                store,
                workspace_id=args["workspace_id"],
                session_id=args.get("session_id"),
                ttl_seconds=args.get("ttl_seconds"),
                metadata=args.get("metadata", {}),
            )
        case "session.close":
            return await coordination.session_close(store, args["session_id"])
        case "session.handoff":
            return await coordination.session_handoff(
                store,
                session_id=args["session_id"],
                from_agent=args["from_agent"],
                to_agent=args["to_agent"],
                message=args["message"],
            )
        case "session.checkpoint":
            return await coordination.session_checkpoint(
                store,
                session_id=args["session_id"],
                label=args["label"],
                include_state=args.get("include_state", False),
            )
        case "session.restore":
            return await coordination.session_restore(store, args["checkpoint_id"])

        # --- events ---
        case "events.subscribe":
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
# Tool schemas                                                         #
# ------------------------------------------------------------------ #

_ALL_TOOLS: list[Tool] = [
    # workspace
    Tool(name="workspace.register", description="Register (or return) a workspace root. §4.1",
         inputSchema={"type": "object", "required": ["root_uri"],
                      "properties": {"root_uri": {"type": "string"},
                                     "name": {"type": "string"},
                                     "metadata": {"type": "object"}}}),
    Tool(name="workspace.index", description="Trigger indexing of a workspace. §4.1",
         inputSchema={"type": "object", "required": ["workspace_id"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "paths": {"type": "array", "items": {"type": "string"}},
                                     "wait": {"type": "boolean"}}}),
    Tool(name="workspace.invalidate", description="Mark chunks from paths as stale. §4.1",
         inputSchema={"type": "object", "required": ["workspace_id", "paths"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "paths": {"type": "array", "items": {"type": "string"}}}}),
    Tool(name="workspace.list_chunks", description="List chunks in a workspace (OPTIONAL). §4.1",
         inputSchema={"type": "object", "required": ["workspace_id"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "filters": {"type": "object"},
                                     "cursor": {"type": "string"}}}),
    # retrieval
    Tool(name="context.search", description="Semantic search over a workspace. §4.2",
         inputSchema={"type": "object", "required": ["workspace_id", "query"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "query": {"type": "string"},
                                     "k": {"type": "integer", "default": 5},
                                     "filters": {"type": "object"}}}),
    Tool(name="context.get_chunk", description="Retrieve a chunk by ID. §4.2",
         inputSchema={"type": "object", "required": ["chunk_id"],
                      "properties": {"chunk_id": {"type": "string"}}}),
    Tool(name="context.pack", description="Assemble a context bundle under a token budget. §4.2",
         inputSchema={"type": "object", "required": ["workspace_id", "intent", "budget_tokens"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "intent": {"type": "string"},
                                     "budget_tokens": {"type": "integer"},
                                     "include_state": {"type": "boolean", "default": False}}}),
    # state
    Tool(name="state.set", description="Write a state entry. §4.3",
         inputSchema={"type": "object", "required": ["key", "value", "scope"],
                      "properties": {"key": {"type": "string"}, "value": {},
                                     "scope": {"type": "string", "enum": ["agent", "session", "global"]},
                                     "workspace_id": {"type": "string"}, "session_id": {"type": "string"},
                                     "agent_id": {"type": "string"}, "ttl_seconds": {"type": "integer"},
                                     "if_version": {"type": "integer"}}}),
    Tool(name="state.get", description="Read a state entry (scope-resolved). §4.3 §5.1",
         inputSchema={"type": "object", "required": ["key"],
                      "properties": {"key": {"type": "string"},
                                     "scope": {"type": "string", "enum": ["agent", "session", "global"]},
                                     "workspace_id": {"type": "string"}, "session_id": {"type": "string"},
                                     "agent_id": {"type": "string"}}}),
    Tool(name="state.list", description="List state entries with optional prefix filter. §4.3",
         inputSchema={"type": "object", "properties": {
             "prefix": {"type": "string"}, "scope": {"type": "string"},
             "workspace_id": {"type": "string"}, "session_id": {"type": "string"},
             "agent_id": {"type": "string"}, "cursor": {"type": "string"}}}),
    Tool(name="state.delete", description="Delete a state entry. §4.3",
         inputSchema={"type": "object", "required": ["key", "scope"],
                      "properties": {"key": {"type": "string"},
                                     "scope": {"type": "string", "enum": ["agent", "session", "global"]},
                                     "workspace_id": {"type": "string"}, "session_id": {"type": "string"},
                                     "agent_id": {"type": "string"}, "if_version": {"type": "integer"}}}),
    # coordination
    Tool(name="session.open", description="Open or materialize a session. §4.4",
         inputSchema={"type": "object", "required": ["workspace_id"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "session_id": {"type": "string"},
                                     "ttl_seconds": {"type": "integer"},
                                     "metadata": {"type": "object"}}}),
    Tool(name="session.close", description="Close a session. §4.4",
         inputSchema={"type": "object", "required": ["session_id"],
                      "properties": {"session_id": {"type": "string"}}}),
    Tool(name="session.handoff", description="Pass control between agents. §4.4",
         inputSchema={"type": "object", "required": ["session_id", "from_agent", "to_agent", "message"],
                      "properties": {"session_id": {"type": "string"}, "from_agent": {"type": "string"},
                                     "to_agent": {"type": "string"}, "message": {}}}),
    Tool(name="session.checkpoint", description="Create a named session checkpoint. §4.4",
         inputSchema={"type": "object", "required": ["session_id", "label"],
                      "properties": {"session_id": {"type": "string"}, "label": {"type": "string"},
                                     "include_state": {"type": "boolean"}}}),
    Tool(name="session.restore", description="Restore a session from a checkpoint. §4.4",
         inputSchema={"type": "object", "required": ["checkpoint_id"],
                      "properties": {"checkpoint_id": {"type": "string"}}}),
    # events
    Tool(name="events.subscribe", description="Subscribe to workspace events. §4.5",
         inputSchema={"type": "object", "required": ["workspace_id"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "types": {"type": "array", "items": {"type": "string"}},
                                     "session_id": {"type": "string"},
                                     "since": {"type": "string"}}}),
    Tool(name="events.unsubscribe", description="Unsubscribe from events. §4.5",
         inputSchema={"type": "object", "required": ["subscription_id"],
                      "properties": {"subscription_id": {"type": "string"}}}),
]


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

async def _main() -> None:
    db_path = os.environ.get("OCP_DB_PATH", "ocp.db")
    app, store, embedder, tokenizer = build_server(db_path)
    await store.setup()
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
