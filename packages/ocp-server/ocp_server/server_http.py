"""OCP HTTP/SSE server — multi-client transport with Bearer auth.

Usage
-----
    ocp-server-http

Environment variables
---------------------
OCP_HOST               Bind address (default: 0.0.0.0)
OCP_PORT               Listen port  (default: 8080)
OCP_DATABASE_URL       PostgreSQL DSN — if set, uses Postgres; else SQLite
OCP_DB_PATH            SQLite DB path (default: ocp.db, ignored if Postgres)
OCP_API_KEYS           Comma-separated valid Bearer tokens (empty = dev/open mode)
OCP_API_KEY_WORKSPACES Per-key workspace restrictions (see auth.py)
OCP_WATCH              Set to 0 to disable file watching (default: 1)
OCP_EMBEDDER           hash | fastembed | openai (default: hash)

Routes
------
GET  /health          liveness probe → {"status":"ok","transport":"http"}
GET  /sse             open SSE stream; client receives server events here
POST /sse/message     client POSTs JSON-RPC messages here
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import uvicorn
from mcp.server.sse import SseServerTransport
from starlette.requests import Request

from ocp_server.auth import (
    AuthConfig,
    _DEV_CONTEXT,
    load_auth_config,
    reset_auth_context,
    set_auth_context,
)
from ocp_server.server import _ttl_cleanup_loop, _file_watch_loop

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Starlette app factory                                                #
# ------------------------------------------------------------------ #

def make_app(mcp_server: Any, auth_cfg: AuthConfig) -> Any:
    """Build a pure ASGI app — no Starlette Route wrapping.

    SseServerTransport manages its own HTTP response lifecycle (streaming),
    so we route at the raw ASGI level to avoid Starlette trying to send a
    second response after the SSE handler has already flushed bytes.
    """
    sse = SseServerTransport("/sse/message")

    _UNAUTH = json.dumps(
        {"error": {"code": "UNAUTHORISED", "message": "Invalid or missing Bearer token"}}
    ).encode()

    async def _send_401(send: Any) -> None:
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"www-authenticate", b"Bearer")]})
        await send({"type": "http.response.body", "body": _UNAUTH})

    async def asgi_app(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        path = scope.get("path", "")

        if path == "/health" and scope.get("method", "GET") == "GET":
            body = b'{"status":"ok","transport":"http"}'
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})
            return

        # Auth check for all other routes
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()
        if auth_header.startswith("Bearer "):
            key = auth_header.removeprefix("Bearer ").strip()
            ctx = auth_cfg.validate_key(key) if auth_cfg.enabled else _DEV_CONTEXT
        else:
            ctx = None if auth_cfg.enabled else _DEV_CONTEXT

        if ctx is None:
            await _send_401(send)
            return

        token = set_auth_context(ctx)
        try:
            if path == "/sse":
                async with sse.connect_sse(scope, receive, send) as (read, write):
                    await mcp_server.run(
                        read, write, mcp_server.create_initialization_options()
                    )
            elif path.startswith("/sse/message"):
                await sse.handle_post_message(scope, receive, send)
            else:
                await send({"type": "http.response.start", "status": 404,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"Not found"})
        finally:
            reset_auth_context(token)

    return asgi_app


def _resolve_auth(request: Request, cfg: AuthConfig) -> Any:
    """Return AuthContext for a valid request, or None if auth fails."""
    if not cfg.enabled:
        return _DEV_CONTEXT
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    key = header.removeprefix("Bearer ").strip()
    return cfg.validate_key(key)


# ------------------------------------------------------------------ #
# Store factory                                                        #
# ------------------------------------------------------------------ #

def _make_store(db_url: str, db_path: str) -> Any:
    if db_url and not db_url.startswith("sqlite"):
        try:
            from ocp_server.storage.postgres import PostgresStore
            log.info("Using PostgreSQL backend: %s", db_url.split("@")[-1])
            return PostgresStore(db_url)
        except ImportError:
            log.warning(
                "asyncpg not installed (pip install ocp-server[postgres]) — "
                "falling back to SQLite"
            )
    from ocp_server.storage.sqlite import SQLiteStore
    log.info("Using SQLite backend: %s", db_path)
    return SQLiteStore(db_path)


# ------------------------------------------------------------------ #
# MCP server wired to an external store                               #
# ------------------------------------------------------------------ #

def build_mcp_server(store: Any) -> tuple[Any, Any, Any]:
    """Return (mcp_app, embedder, emit_fn) wired to the given store."""
    from mcp.server import Server
    from mcp.types import LoggingMessageNotification, LoggingMessageNotificationParams, TextContent, Tool
    from ocp_server.embedder import make_embedder, Tokenizer
    from ocp_server.server import _dispatch, _ALL_TOOLS, _handle_known, _ocp_error
    from ocp_server.tools.events import emit_event
    from ocp_server.auth import PermissionDeniedError

    embedder = make_embedder()
    tokenizer = Tokenizer()

    app = Server(
        name="ocp-server",
        version="0.1.0",
        instructions=(
            "OCP/0.1 reference server — profiles: ocp/0.1 — conformance: core+coordination. "
            "context.search: k capped at 20. "
            "Embedding backend: OCP_EMBEDDER=hash|fastembed|openai (default: hash)."
        ),
    )

    async def _send_notification(envelope: dict) -> None:
        try:
            ctx = app.request_context
            await ctx.session.send_notification(
                LoggingMessageNotification(
                    method="notifications/message",
                    params=LoggingMessageNotificationParams(
                        level="info", logger="ocp.events", data=envelope,
                    ),
                )
            )
        except (LookupError, Exception):
            pass

    async def emit_fn(workspace_id: str, event_type: str, payload: dict) -> None:
        async def _cb(sub_id: str, envelope: dict) -> None:
            await _send_notification(envelope)
        await emit_event(store, workspace_id, event_type, payload, _cb)

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return _ALL_TOOLS

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            result = await _dispatch(name, arguments, store, embedder, tokenizer, emit_fn)
        except PermissionDeniedError as exc:
            result = _ocp_error("PERMISSION_DENIED", str(exc))
        except Exception as exc:
            err = _handle_known(exc)
            result = err if err else (_ for _ in ()).throw(exc)
        return [TextContent(type="text", text=json.dumps(result))]

    return app, embedder, emit_fn


# ------------------------------------------------------------------ #
# Entry point                                                          #
# ------------------------------------------------------------------ #

async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    host = os.environ.get("OCP_HOST", "0.0.0.0")
    port = int(os.environ.get("OCP_PORT", "8080"))
    watch = os.environ.get("OCP_WATCH", "1") != "0"
    auth_cfg = load_auth_config()

    store = _make_store(
        os.environ.get("OCP_DATABASE_URL", ""),
        os.environ.get("OCP_DB_PATH", "ocp.db"),
    )
    await store.setup()

    mcp_app, embedder, emit_fn = build_mcp_server(store)

    if not auth_cfg.enabled:
        log.warning(
            "OCP_API_KEYS is not set — server running in OPEN/DEV mode. "
            "Do NOT expose to untrusted networks."
        )
    else:
        log.info("Auth enabled — %d key(s) configured", len(auth_cfg.key_map))

    ttl_task = asyncio.create_task(_ttl_cleanup_loop(store, emit_fn))
    watch_task = asyncio.create_task(_file_watch_loop(store, embedder, emit_fn)) if watch else None

    app = make_app(mcp_app, auth_cfg)

    log.info("OCP HTTP server starting on http://%s:%d", host, port)
    log.info("  Health:   GET  /health")
    log.info("  SSE:      GET  /sse          (Authorization: Bearer <key>)")
    log.info("  Messages: POST /sse/message  (Authorization: Bearer <key>)")

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        ttl_task.cancel()
        if watch_task:
            watch_task.cancel()
        await asyncio.gather(
            ttl_task, watch_task or asyncio.sleep(0), return_exceptions=True
        )


def main() -> None:
    try:
        asyncio.run(_main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
