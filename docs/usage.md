# OCP Usage Guide

This guide walks through every major feature of the Open Context Protocol with runnable Python examples. All examples use the `ocp-client` async SDK and assume the server is running.

**Prerequisites:** Python 3.11+. Install the packages from PyPI:

```bash
pip install ocp-server ocp-client
```

Alternatively, run the server without a permanent install using `uvx ocp-server` and install only the client:

```bash
pip install ocp-client   # client SDK only
uvx ocp-server           # runs the server from PyPI on demand
```

---

## Table of contents

1. [Connecting to the server](#1-connecting-to-the-server)
2. [Workspaces](#2-workspaces)
3. [Retrieval](#3-retrieval)
4. [State management](#4-state-management)
5. [Session coordination](#5-session-coordination)
6. [Events](#6-events)
7. [Authentication](#7-authentication)
8. [Error handling](#8-error-handling)
9. [Common patterns](#9-common-patterns)
10. [Running in Docker](#10-running-in-docker)

---

## 1. Connecting to the server

OCP uses MCP's transport layer. The Python client supports **stdio transport** (spawning the server as a subprocess), which is the standard mode for agent integrations.

```python
import asyncio
from ocp_client import OCPClient

async def main():
    # stdio — spawns ocp-server as a child process
    async with OCPClient.stdio(["ocp-server"]) as client:
        # client is ready; use it here
        print("Connected")

asyncio.run(main())
```

### Passing environment variables to the server

```python
async with OCPClient.stdio(
    ["ocp-server"],
    env={
        "OCP_DB_PATH": "/var/lib/ocp/prod.db",
        "OCP_EMBEDDER": "fastembed",
        "OCP_API_KEY": "my-bearer-token",
    },
) as client:
    ...
```

### Using the HTTP server

When the server is running in HTTP mode (`ocp-server-http`), connect via an MCP HTTP client. The stdio client is recommended for single-agent use; HTTP is for multi-client or cross-process scenarios.

```bash
# Start the HTTP server
OCP_API_KEYS=my-key ocp-server-http
# Listening on http://0.0.0.0:8080
```

---

## 2. Workspaces

A workspace is the top-level container for indexed content — a repository, documentation set, knowledge base, or any directory of files.

### Register a workspace

`workspace.register` is idempotent: calling it twice with the same URI returns the same `workspace_id`.

```python
ws = await client.workspace_register(
    root_uri="file:///Users/rajesh/code/my-project",
    name="my-project",           # optional human label
    metadata={"team": "backend"},  # optional arbitrary metadata
)

print(ws.workspace_id)   # ws_a3f2e1d0c4b58790  (deterministic from URI)
print(ws.name)
```

### Index a workspace

Indexing walks the workspace root, splits text files into ~4 KB chunks, embeds each chunk, and stores them for retrieval.

Supported file extensions: `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.go`, `.rs`, `.java`, `.c`, `.cpp`, `.h`, `.rb`, `.md`, `.yaml`, `.yml`, `.json`, `.toml`, `.txt`

```python
result = await client.workspace_index(ws.workspace_id)
print(f"Indexed {result.indexed} chunks ({result.skipped} unchanged) in {result.duration_ms} ms")
```

**Output example:**
```
Indexed 142 chunks (0 unchanged) in 1834 ms
```

#### Index specific paths only

```python
# Reindex only the auth module
result = await client.workspace_index(ws.workspace_id, paths=["src/auth/"])
```

#### Background indexing

```python
# Returns immediately; indexing continues in the background
await client.workspace_index(ws.workspace_id, wait=False)
# Watch for "index.progress" and "chunk.indexed" events (see §6)
```

### Explicit invalidation

Mark chunks from specific paths as stale when you know files have changed outside the file watcher:

```python
result = await client.workspace_invalidate(ws.workspace_id, paths=["src/auth/jwt.py"])
print(f"Invalidated {result.invalidated} chunks")
```

> **Automatic invalidation:** When `OCP_WATCH=1` (the default), the server monitors registered workspace roots with `watchfiles`. Any file change automatically marks affected chunks stale and emits a `chunk.invalidated` event — no manual call needed.

### List chunks

```python
chunks, next_cursor = await client.workspace_list_chunks(ws.workspace_id)
for chunk in chunks:
    print(f"{chunk.id[:12]}  {chunk.source.uri}:{chunk.source.range.start_line if chunk.source.range else '-'}")
```

**Output example:**
```
a3f2e1d0c4b5  file:///Users/rajesh/code/my-project/src/auth/jwt.py:1
b7e9c2f1a840  file:///Users/rajesh/code/my-project/src/auth/jwt.py:45
d1a4b6c8e0f2  file:///Users/rajesh/code/my-project/README.md:1
```

Cursor-based pagination for large workspaces:

```python
all_chunks = []
cursor = None
while True:
    page, cursor = await client.workspace_list_chunks(ws.workspace_id, cursor=cursor)
    all_chunks.extend(page)
    if cursor is None:
        break
print(f"Total: {len(all_chunks)} chunks")
```

---

## 3. Retrieval

### Semantic search

`context.search` embeds the query and returns the top-k most similar chunks by cosine similarity.

```python
results = await client.context_search(
    ws.workspace_id,
    query="how does JWT token validation work",
    k=5,
)

for chunk, score in zip(results.chunks, results.scores):
    loc = ""
    if chunk.source.range:
        loc = f":{chunk.source.range.start_line}–{chunk.source.range.end_line}"
    print(f"[{score:.3f}] {chunk.source.uri}{loc}")
    print(chunk.content[:300])
    print()
```

**Output example:**
```
[0.847] file:///Users/rajesh/code/my-project/src/auth/jwt.py:12–58
def verify_token(token: str) -> dict:
    """Verify a JWT and return its claims."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    ...

[0.731] file:///Users/rajesh/code/my-project/docs/auth.md:1–30
## Authentication
Tokens are signed with HMAC-SHA256...
```

> **Note:** `k` is capped at 20 by the reference server (§4.2).

#### Filter by language or kind

```python
results = await client.context_search(
    ws.workspace_id,
    query="database connection pool",
    k=10,
    filters={"language": "python"},
)
```

### Fetch a specific chunk

```python
chunk_id = results.chunks[0].id
chunk = await client.context_get_chunk(chunk_id)

print(chunk.content)
print(f"Version: {chunk.version}")
print(f"Hash:    {chunk.source.content_hash}")
print(f"Stale:   {chunk.metadata.get('stale', False)}")
```

Stale chunks (file changed since indexing) return an error code `STALE` — handle it explicitly:

```python
from ocp_client.types import OCPError

try:
    chunk = await client.context_get_chunk(chunk_id)
except OCPError as e:
    if e.code == "STALE":
        # Re-index and retry
        await client.workspace_index(ws.workspace_id)
        chunk = await client.context_get_chunk(chunk_id)
    else:
        raise
```

### Token-budget-aware context packing

`context.pack` assembles a context bundle under a token budget. It is ideal for injecting relevant context into an LLM prompt without guessing how much to include.

```python
pack = await client.context_pack(
    ws.workspace_id,
    intent="explain how authentication middleware works",
    budget_tokens=4096,
    include_state=True,  # also includes global state entries
)

print(f"Packed {pack.tokens} tokens from {len(pack.chunk_ids)} chunks")
print()
print(pack.context)   # ready to inject into a prompt
```

**Output example:**
```
Packed 3847 tokens from 9 chunks

[src/auth/middleware.py:1-42]
class AuthMiddleware:
    """ASGI middleware that validates Bearer tokens..."""
    ...

[src/auth/jwt.py:12-58]
def verify_token(token: str) -> dict:
    ...
```

---

## 4. State management

State entries are typed key-value pairs. OCP defines three scopes:

| Scope | Lifetime | Requires |
|---|---|---|
| `global` | Persists until deleted | `workspace_id` |
| `session` | Bounded to a session | `workspace_id` + `session_id` |
| `agent` | Crosses workspace boundaries | `agent_id` |

### Write and read state

```python
# Global scope — configuration shared across all sessions
await client.state_set(
    key="config.language",
    value="python",
    scope="global",
    workspace_id=ws.workspace_id,
)

entry = await client.state_get(
    key="config.language",
    scope="global",
    workspace_id=ws.workspace_id,
)
print(entry.value)    # "python"
print(entry.version)  # 1
```

### Scope resolution

`state.get` without an explicit scope auto-resolves: **agent → session → global**

```python
sess = await client.session_open(ws.workspace_id)

await client.state_set("x", "global_val", scope="global", workspace_id=ws.workspace_id)
await client.state_set("x", "session_val", scope="session",
                        workspace_id=ws.workspace_id, session_id=sess.session_id)

# No scope specified — session takes precedence over global
entry = await client.state_get("x", workspace_id=ws.workspace_id,
                                session_id=sess.session_id)
print(entry.value)   # "session_val"
```

### Session-scoped state

```python
sess = await client.session_open(ws.workspace_id)

# Store an agent's working plan
await client.state_set(
    key="plan",
    value={"steps": ["analyse", "implement", "test"], "current": 0},
    scope="session",
    workspace_id=ws.workspace_id,
    session_id=sess.session_id,
)

# Update: increment current step
plan = (await client.state_get("plan", scope="session",
                                workspace_id=ws.workspace_id,
                                session_id=sess.session_id)).value
plan["current"] += 1
await client.state_set("plan", plan, scope="session",
                        workspace_id=ws.workspace_id,
                        session_id=sess.session_id)
```

### Agent-scoped state

Agent state is not tied to any workspace — useful for cross-project memory:

```python
await client.state_set(
    key="agent.system-context",
    value="You are a senior Python engineer. Prefer simplicity over abstraction.",
    scope="agent",
    agent_id="agent-planner",
)

entry = await client.state_get("agent.system-context", scope="agent", agent_id="agent-planner")
print(entry.value)
```

### Optimistic concurrency (versioned writes)

Prevent lost updates when multiple agents may write the same key:

```python
# First write
r1 = await client.state_set("counter", 0, scope="global", workspace_id=ws.workspace_id)
print(r1.version)  # 1

# Conditional update — succeeds only if current version == 1
r2 = await client.state_set("counter", 1, scope="global",
                              workspace_id=ws.workspace_id, if_version=r1.version)
print(r2.version)  # 2

# Fails with CONFLICT if another agent updated it in the meantime
from ocp_client.types import OCPError
try:
    await client.state_set("counter", 99, scope="global",
                            workspace_id=ws.workspace_id, if_version=1)  # stale version
except OCPError as e:
    print(e.code)     # CONFLICT
    print(e.message)  # version mismatch

# if_version=0 means "only write if key does not yet exist"
try:
    await client.state_set("new_key", "value", scope="global",
                            workspace_id=ws.workspace_id, if_version=0)
except OCPError as e:
    print(e.code)  # CONFLICT — key already existed
```

### TTL (time-to-live)

```python
import asyncio

await client.state_set(
    key="temp.cache",
    value={"results": [1, 2, 3]},
    scope="session",
    workspace_id=ws.workspace_id,
    session_id=sess.session_id,
    ttl_seconds=60,  # expires in 60 seconds
)
```

The server purges expired entries every 60 seconds.

### List and delete state

```python
# List all session-scope entries
entries, cursor = await client.state_list(
    scope="session",
    workspace_id=ws.workspace_id,
    session_id=sess.session_id,
)
for e in entries:
    print(f"{e.key} = {e.value!r}  (v{e.version})")

# List by key prefix
entries, _ = await client.state_list(prefix="config.", scope="global",
                                      workspace_id=ws.workspace_id)

# Delete
deleted = await client.state_delete("temp.cache", scope="session",
                                     workspace_id=ws.workspace_id,
                                     session_id=sess.session_id)
print(deleted)  # True

# Conditional delete
deleted = await client.state_delete("counter", scope="global",
                                     workspace_id=ws.workspace_id,
                                     if_version=3)  # only delete if version == 3
```

---

## 5. Session coordination

Sessions represent a bounded unit of coordinated work — often spanning multiple agent invocations.

### Open and close a session

```python
import uuid

sess = await client.session_open(
    ws.workspace_id,
    session_id=str(uuid.uuid4()),   # client-generated (recommended)
    ttl_seconds=3600,               # auto-close after 1 hour
    metadata={"task": "refactor-auth"},
)
print(sess.session_id)

# ... do work ...

closed = await client.session_close(sess.session_id)
print(closed)  # True
```

> Sessions are auto-materialised by `state.set` only — `session.handoff` requires the session to already exist. Use `session.open` when you want explicit control over TTL or metadata, or before calling `session.handoff`.

### Agent handoff

Pass control between agents within a session:

```python
# Planner agent finishes its work and hands off to the executor
handoff = await client.session_handoff(
    session_id=sess.session_id,
    from_agent="planner",
    to_agent="executor",
    message={
        "task": "deploy branch feature/auth-refactor to staging",
        "approved_by": "planner",
        "context_chunks": [chunk.id for chunk in results.chunks[:3]],
    },
)
print(handoff.handoff_id)

# Executor agent reads the handoff from agent-scoped state
msg = await client.state_get(
    key=f"handoff.{handoff.handoff_id}",
    scope="agent",
    agent_id="executor",
)
if msg:
    print(msg.value)  # {"task": "deploy ...", "approved_by": "planner", ...}
```

### Checkpoints and restore

Save a named snapshot of session state for recovery or branching:

```python
# Checkpoint before a risky operation
ckpt = await client.session_checkpoint(
    sess.session_id,
    label="before-deploy",
    include_state=True,  # snapshot all session-scoped state entries too
)
print(ckpt.checkpoint_id)

# ... attempt the operation ...

# Something went wrong — restore the session from the checkpoint
restored = await client.session_restore(ckpt.checkpoint_id)
print(restored.session_id)  # new session_id with copied state
```

---

## 6. Events

OCP events are delivered as MCP notifications over the same connection. Subscribe to specific event types for real-time updates.

### Event types

| Event | Trigger |
|---|---|
| `chunk.invalidated` | File changed, explicit invalidation call, or hash mismatch on reindex |
| `chunk.indexed` | Indexing completed |
| `state.changed` | `state.set` or `state.delete` |
| `session.handoff` | `session.handoff` called |
| `session.closed` | `session.close` called or TTL expired |
| `index.progress` | Indexing progress update (0.0–1.0) |

### Subscribe

```python
sub = await client.events_subscribe(
    ws.workspace_id,
    types=["chunk.invalidated", "state.changed"],  # filter; omit for all types
)
print(sub.subscription_id)
```

### Replay missed events

```python
# Catch up on events since a previous timestamp
sub = await client.events_subscribe(
    ws.workspace_id,
    since="2026-05-11T00:00:00Z",
)
```

### Receiving notifications

OCP events arrive as MCP `notifications/message` log notifications with `logger="ocp.events"`. Handling them requires access to the underlying MCP session:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import json

async def on_notification(method, params):
    if method == "notifications/message":
        if getattr(params, "logger", None) == "ocp.events":
            envelope = params.data
            print(f"Event: {envelope['type']}")
            print(f"  workspace: {envelope['workspace_id']}")
            print(f"  payload:   {envelope['payload']}")

params = StdioServerParameters(command="ocp-server")
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        session.on_notification = on_notification
        await session.initialize()
        # ... your code ...
```

### Unsubscribe

```python
await client.events_unsubscribe(sub.subscription_id)
```

> **Delivery guarantee:** At-most-once (§7.3). Events may be lost during server teardown. Use the `since` parameter to replay on reconnect.

---

## 7. Authentication

### Development mode (default)

When `OCP_API_KEYS` is not set, the server runs in open mode — suitable for local development only.

```bash
ocp-server   # warning: "running in open/dev mode"
```

### Enabling auth

```bash
# Server
export OCP_API_KEYS=key-alpha,key-beta
ocp-server

# Client (stdio) — pass the key in the server's environment
async with OCPClient.stdio(["ocp-server"], env={"OCP_API_KEY": "key-alpha"}) as client:
    ...
```

### Per-key workspace restrictions

Limit each API key to specific workspaces:

```bash
# key-alpha can access ws_id1 and ws_id2; key-beta only ws_id3
export OCP_API_KEYS=key-alpha,key-beta
export OCP_API_KEY_WORKSPACES="key-alpha:ws_id1,ws_id2;key-beta:ws_id3"
```

Attempting to access a workspace outside the key's allowed list returns `PERMISSION_DENIED`.

---

## 8. Error handling

All tool errors are returned as structured JSON and raised as `OCPError` by the client:

```python
from ocp_client.types import OCPError

try:
    chunk = await client.context_get_chunk("nonexistent-id")
except OCPError as e:
    print(e.code)     # CHUNK_NOT_FOUND
    print(e.message)  # "chunk not found: nonexistent-id"
```

### Error codes

| Code | Meaning |
|---|---|
| `WORKSPACE_NOT_FOUND` | The `workspace_id` does not exist |
| `CHUNK_NOT_FOUND` | The `chunk_id` does not exist |
| `STALE` | Chunk exists but has been invalidated (file changed) |
| `CONFLICT` | Optimistic lock failure (`if_version` mismatch) |
| `PERMISSION_DENIED` | API key not allowed to access this workspace |
| `SESSION_NOT_FOUND` | The `session_id` does not exist |
| `METHOD_NOT_FOUND` | Unknown tool name |
| `NOT_FOUND` | Generic not-found (e.g. subscription) |

---

## 9. Common patterns

### Pattern 1: RAG pipeline

```python
import asyncio
from ocp_client import OCPClient

async def rag_context(query: str, budget_tokens: int = 4096) -> str:
    async with OCPClient.stdio(["ocp-server"]) as client:
        ws = await client.workspace_register("file:///path/to/repo")
        result = await client.workspace_index(ws.workspace_id)
        print(f"Indexed {result.indexed} chunks ({result.skipped} unchanged) in {result.duration_ms} ms")

        pack = await client.context_pack(
            ws.workspace_id,
            intent=query,
            budget_tokens=budget_tokens,
        )
        return pack.context

context = asyncio.run(rag_context("how does the auth middleware work?"))
print(context)
```

### Pattern 2: Multi-agent pipeline with shared state

```python
import asyncio, uuid
from ocp_client import OCPClient

async def multi_agent_pipeline():
    async with OCPClient.stdio(["ocp-server"]) as client:
        ws = await client.workspace_register("file:///path/to/repo")
        sess = await client.session_open(ws.workspace_id, session_id=str(uuid.uuid4()))

        # Agent 1: Planner — writes a plan to session state
        await client.state_set(
            "plan",
            {"steps": ["analyse", "implement", "test"], "current": 0},
            scope="session",
            workspace_id=ws.workspace_id,
            session_id=sess.session_id,
        )

        # Agent 2: Executor — reads and acts on the plan
        plan_entry = await client.state_get(
            "plan", scope="session",
            workspace_id=ws.workspace_id, session_id=sess.session_id,
        )
        plan = plan_entry.value
        print(f"Executor running step: {plan['steps'][plan['current']]}")

        # Hand off back to planner
        await client.session_handoff(
            session_id=sess.session_id,
            from_agent="executor",
            to_agent="planner",
            message={"status": "step 0 complete"},
        )

        await client.session_close(sess.session_id)

asyncio.run(multi_agent_pipeline())
```

### Pattern 3: Resilient agent with checkpoints

```python
import asyncio, uuid
from ocp_client import OCPClient

async def resilient_agent(task: str):
    async with OCPClient.stdio(["ocp-server"]) as client:
        ws = await client.workspace_register("file:///path/to/repo")
        sess = await client.session_open(ws.workspace_id, session_id=str(uuid.uuid4()))

        # Save state before risky operation
        await client.state_set("task", task, scope="session",
                                workspace_id=ws.workspace_id, session_id=sess.session_id)
        ckpt = await client.session_checkpoint(sess.session_id,
                                               label="before-deploy", include_state=True)

        try:
            # ... risky operation ...
            raise RuntimeError("simulated failure")
        except Exception:
            # Restore and retry from checkpoint
            print("Restoring from checkpoint...")
            restored = await client.session_restore(ckpt.checkpoint_id)
            state_entry = await client.state_get("task", scope="session",
                                                  workspace_id=ws.workspace_id,
                                                  session_id=restored.session_id)
            print(f"Restored task: {state_entry.value}")

asyncio.run(resilient_agent("deploy feature/auth-refactor"))
```

### Pattern 4: Watch for file changes

```python
import asyncio
from ocp_client import OCPClient
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def watch_workspace():
    params = StdioServerParameters(command="ocp-server")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            client = OCPClient(session)

            ws = await client.workspace_register("file:///path/to/repo")
            await client.workspace_index(ws.workspace_id)
            sub = await client.events_subscribe(ws.workspace_id, types=["chunk.invalidated"])

            # In your notification handler:
            # - Receive chunk.invalidated event
            # - Trigger re-index of changed paths
            # - Emit updated context to the agent
            print(f"Watching workspace {ws.workspace_id}")
            print(f"Subscription: {sub.subscription_id}")
            await asyncio.sleep(60)  # watch for 60 seconds

asyncio.run(watch_workspace())
```

---

## 10. Running in Docker

The OCP server is published as a Docker image at `ghcr.io/rajesh1213/ocp:latest`.

### Pull and run the HTTP server

```bash
docker pull ghcr.io/rajesh1213/ocp:latest

# Start the HTTP/SSE server on port 8080
docker run --rm -p 8080:8080 \
  -e OCP_API_KEYS=my-secret-key \
  ghcr.io/rajesh1213/ocp:latest

# Health check
curl http://localhost:8080/health
```

### Mount a local directory as a workspace

To index files from the host, mount the directory into the container:

```bash
docker run --rm -p 8080:8080 \
  -e OCP_API_KEYS=my-secret-key \
  -v /path/to/your/repo:/workspace:ro \
  ghcr.io/rajesh1213/ocp:latest
```

Then register the workspace using the container-internal path:

```python
ws = await client.workspace_register("file:///workspace")
```

### Connect a client to the HTTP server

When the server is running in HTTP mode, use an MCP HTTP/SSE client instead of the stdio client. You can also use the stdio client to spawn a local `ocp-server` that proxies to the HTTP backend, or connect directly using an HTTP-capable MCP client library.

```bash
# With Docker Compose (PostgreSQL + OCP)
cp .env.example .env
# Edit .env: set OCP_API_KEYS and optionally OCP_EMBEDDER=fastembed

docker compose up
# OCP server:  http://localhost:8080
# Health:      http://localhost:8080/health
# PostgreSQL:  localhost:5432
```

### Environment variables in Docker

All [configuration variables](../README.md#configuration-reference) are passed as `-e` flags or via `--env-file`:

```bash
docker run --rm -p 8080:8080 \
  --env-file .env \
  ghcr.io/rajesh1213/ocp:latest
```

---

## Conformance levels

When building your own OCP server, advertise the correct conformance level:

```json
{
  "name": "my-ocp-server",
  "version": "1.0.0",
  "profiles": ["ocp/0.1"],
  "conformance": "core+coordination"
}
```

Run the conformance suite against your implementation:

```bash
OCP_SERVER_CMD=/path/to/your-ocp-server bash scripts/run-conformance.sh
```

The suite covers all normative `MUST` and `SHOULD` requirements from [OCP-0001](../spec/OCP-0001.md).
