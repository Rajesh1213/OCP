# ocp-client

Python async client SDK for the [Open Context Protocol (OCP)](https://github.com/Rajesh1213/OCP) — a vendor-neutral protocol for sharing retrievable context, persistent state, and invalidation events across AI agents, models, and frameworks.

## Install

```bash
pip install ocp-client
```

## Quick start

```python
import asyncio
from ocp_client import OCPClient

async def main():
    async with OCPClient.stdio(["ocp-server"]) as client:
        # Register and index a workspace
        ws = await client.workspace_register("file:///path/to/your/repo")
        await client.workspace_index(ws.workspace_id)

        # Semantic search
        results = await client.context_search(ws.workspace_id, "authentication middleware")
        for chunk, score in zip(results.chunks, results.scores):
            print(f"[{score:.3f}] {chunk.source.uri}")
            print(chunk.content[:200])

asyncio.run(main())
```

Requires [ocp-server](https://pypi.org/project/ocp-server/) running — install with `pip install ocp-server` or run zero-install with `uvx ocp-server`.

## Features

### Workspaces & retrieval

```python
# Register (idempotent — same URI always returns the same workspace_id)
ws = await client.workspace_register("file:///path/to/repo", name="my-repo")

# Index all supported files (.py, .ts, .go, .md, .yaml, ...)
result = await client.workspace_index(ws.workspace_id)
print(f"Indexed {result.indexed} chunks in {result.duration_ms} ms")

# Semantic k-NN search (k capped at 20)
results = await client.context_search(ws.workspace_id, "JWT token validation", k=10)

# Token-budget-aware context packing — ideal for LLM prompt assembly
pack = await client.context_pack(
    ws.workspace_id,
    intent="explain how authentication works",
    budget_tokens=4096,
)
print(pack.context)   # assembled context string, ready to inject into a prompt
print(pack.tokens)    # actual token count used
```

### Scoped state

```python
# Three scopes: "global", "session", "agent"
await client.state_set("config.model", "claude-3-5-sonnet", scope="global", workspace_id=ws.workspace_id)
entry = await client.state_get("config.model", scope="global", workspace_id=ws.workspace_id)
print(entry.value)    # "claude-3-5-sonnet"
print(entry.version)  # 1

# Optimistic concurrency — fails with CONFLICT if version is stale
result = await client.state_set("counter", 2, scope="global",
                                 workspace_id=ws.workspace_id, if_version=result.version)

# TTL — auto-expires after N seconds
await client.state_set("temp.flag", True, scope="session",
                        workspace_id=ws.workspace_id, session_id=sess.session_id,
                        ttl_seconds=300)
```

### Session coordination

```python
import uuid

# Open a session
sess = await client.session_open(ws.workspace_id, session_id=str(uuid.uuid4()))

# Hand off between agents
handoff = await client.session_handoff(
    session_id=sess.session_id,
    from_agent="planner",
    to_agent="executor",
    message={"task": "deploy to staging"},
)

# Checkpoint before a risky operation
ckpt = await client.session_checkpoint(sess.session_id, label="before-deploy", include_state=True)

# Restore from checkpoint (creates a new session with copied state)
restored = await client.session_restore(ckpt.checkpoint_id)

await client.session_close(sess.session_id)
```

### Events

```python
# Subscribe to workspace events
sub = await client.events_subscribe(
    ws.workspace_id,
    types=["chunk.invalidated", "state.changed"],
)

# Replay events since a timestamp
sub = await client.events_subscribe(ws.workspace_id, since="2026-01-01T00:00:00Z")

await client.events_unsubscribe(sub.subscription_id)
```

### Error handling

```python
from ocp_client.types import OCPError

try:
    chunk = await client.context_get_chunk(chunk_id)
except OCPError as e:
    print(e.code)     # STALE | CHUNK_NOT_FOUND | CONFLICT | PERMISSION_DENIED | ...
    print(e.message)
```

## Connecting to a remote HTTP server

```python
from mcp import ClientSession
from mcp.client.sse import sse_client

async with sse_client("http://localhost:8080/sse",
                      headers={"Authorization": "Bearer my-key"}) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        client = OCPClient(session)
        # ... use client ...
```

## Links

- [GitHub](https://github.com/Rajesh1213/OCP)
- [Protocol spec (OCP-0001)](https://github.com/Rajesh1213/OCP/blob/main/spec/OCP-0001.md)
- [Full usage guide](https://github.com/Rajesh1213/OCP/blob/main/docs/usage.md)
- [ocp-server](https://pypi.org/project/ocp-server/) — reference server implementation

## License

Apache-2.0 — see [LICENSE](https://github.com/Rajesh1213/OCP/blob/main/LICENSE)
