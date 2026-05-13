# Open Context Protocol (OCP)

[![PyPI](https://img.shields.io/pypi/v/ocp-server)](https://pypi.org/project/ocp-server/) [![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**Status:** v0.1.0 — RFC draft | [PyPI](https://pypi.org/project/ocp-server/) | Apache-2.0 (code) · CC-BY 4.0 (spec)

OCP is a protocol for sharing retrievable context, persistent state, and invalidation events across AI agents, models, frameworks, and organizations. It is layered on top of the [Model Context Protocol (MCP)](https://modelcontextprotocol.io): every OCP server is an MCP server, every OCP client is an MCP client.

```
Agent A ──► OCP Client ──► OCP Server ──► SQLite / PostgreSQL
                                │
Agent B ──► OCP Client ──────── │  (same or different process)
```

Where MCP provides transport and tool-call mechanics, OCP adds:

- **Workspaces** — top-level containers of indexed content (repos, docs, ticket archives)
- **Semantic retrieval** — k-NN search over embedded chunks, token-budget-aware packing
- **Scoped state** — typed key-value store with `agent`, `session`, and `global` scopes
- **Session coordination** — handoff between agents, checkpoints, restore
- **Invalidation & events** — real-time notifications when files change or state is updated

---

## Contents

- [Quick start](#quick-start)
- [Installation](#installation)
- [Running the server](#running-the-server)
- [Integrations](docs/integrations.md) — Claude Code, Claude Desktop, Cursor, HTTP/SSE
- [Python client usage](#python-client-usage)
- [Configuration reference](#configuration-reference)
- [Deployment](#deployment)
- [Testing & conformance](#testing--conformance)
- [Repository layout](#repository-layout)
- [Specification](#specification)
- [Contributing](#contributing)

---

## Quick start

No installation required — `uvx` runs the server directly from PyPI:

```bash
# 1. Start the server (zero-install, stdio transport, SQLite, no auth)
uvx ocp-server

# 2. In a separate script, connect a client and search
pip install ocp-client
python - <<'EOF'
import asyncio
from ocp_client import OCPClient

async def main():
    async with OCPClient.stdio(["ocp-server"]) as client:
        ws = await client.workspace_register("file:///path/to/your/repo")
        await client.workspace_index(ws.workspace_id)
        results = await client.context_search(ws.workspace_id, "authentication middleware")
        for chunk, score in zip(results.chunks, results.scores):
            loc = f":{chunk.source.range.start_line}" if chunk.source.range else ""
            print(f"[{score:.3f}] {chunk.source.uri}{loc}")
            print(chunk.content[:200])
            print()

asyncio.run(main())
EOF
```

---

## Installation

**Prerequisites:** Python 3.11+

### pip (recommended)

```bash
pip install ocp-server          # server + CLI commands
pip install ocp-client          # Python async client SDK
```

### Zero-install via uvx

[uv](https://docs.astral.sh/uv/) can run the server directly from PyPI without a dedicated install step:

```bash
uvx ocp-server
uvx ocp-server-http
```

After `pip install ocp-server`, the following CLI commands are available:

| Command | Description |
|---|---|
| `ocp-server` | stdio transport (for agent integrations) |
| `ocp-server-http` | HTTP/SSE transport (for multi-client scenarios) |

The `ocp-client` package is importable as:

```python
from ocp_client import OCPClient
```

### Contributing / development

If you are contributing to OCP itself, clone the repo and use the dev-install script:

```bash
git clone https://github.com/Rajesh1213/OCP.git
cd OCP
curl -LsSf https://astral.sh/uv/install.sh | sh   # skip if uv is already installed
bash scripts/dev-install.sh
source .venv/bin/activate
```

`dev-install.sh` creates a `.venv` and installs all three workspace packages in editable mode:

| Package | Installed as |
|---|---|
| `ocp-server` | `ocp-server` and `ocp-server-http` CLI commands |
| `ocp-client` | importable as `from ocp_client import OCPClient` |
| `ocp-conformance` | `ocp-conformance` CLI + pytest suite |

---

## Running the server

### Stdio (default — for agent integrations)

```bash
ocp-server
```

Listens on stdin/stdout using MCP's stdio transport. This is the standard way to embed the server inside an agent runtime or IDE extension.

```bash
# With a specific database path
OCP_DB_PATH=/var/lib/ocp/data.db ocp-server

# With the fastembed backend (requires: pip install fastembed)
OCP_EMBEDDER=fastembed ocp-server

# With auth enabled
OCP_API_KEYS=my-secret-key OCP_API_KEY=my-secret-key ocp-server
```

### HTTP / SSE (for multi-client scenarios)

```bash
ocp-server-http
# Listening on http://0.0.0.0:8080
# Health check: GET /health
```

```bash
OCP_HOST=127.0.0.1 OCP_PORT=9000 ocp-server-http
```

---

## Python client usage

All client operations are async. See [docs/usage.md](docs/usage.md) for full examples.

### Connect

```python
from ocp_client import OCPClient

# stdio — spawns ocp-server as a subprocess
async with OCPClient.stdio(["ocp-server"]) as client:
    ...

# With environment variables (e.g. auth key)
async with OCPClient.stdio(["ocp-server"], env={"OCP_API_KEY": "my-key"}) as client:
    ...
```

### Workspaces

```python
# Register a workspace (idempotent — same URI returns the same workspace_id)
ws = await client.workspace_register("file:///path/to/repo", name="my-repo")
print(ws.workspace_id)   # ws_<hex>

# Index all supported files (py, ts, js, go, md, yaml, json, ...)
result = await client.workspace_index(ws.workspace_id)
print(f"Indexed {result.indexed} chunks in {result.duration_ms} ms")

# Index specific paths only
await client.workspace_index(ws.workspace_id, paths=["src/auth/"])

# Async indexing (returns immediately)
await client.workspace_index(ws.workspace_id, wait=False)
```

### Retrieval

```python
# Semantic search (k-NN, default k=5)
results = await client.context_search(ws.workspace_id, "JWT token validation", k=10)
for chunk, score in zip(results.chunks, results.scores):
    print(f"[{score:.3f}] {chunk.source.uri}")

# Fetch a specific chunk by ID
chunk = await client.context_get_chunk(chunk_id)
if chunk.metadata.get("stale"):
    print("Chunk is stale — file has changed since indexing")

# Token-budget-aware context packing (ideal for prompt assembly)
pack = await client.context_pack(
    ws.workspace_id,
    intent="explain how authentication works",
    budget_tokens=4096,
    include_state=True,
)
print(pack.context)       # assembled context string
print(pack.tokens)        # actual token count
```

### State management

```python
# Three scopes: "global", "session", "agent"

# Global scope — shared across all sessions in a workspace
await client.state_set("config.model", "gpt-4o", scope="global", workspace_id=ws_id)
entry = await client.state_get("config.model", scope="global", workspace_id=ws_id)
print(entry.value)  # "gpt-4o"

# Session scope — bounded to a session
sess = await client.session_open(ws.workspace_id)
await client.state_set("plan", {"step": 1}, scope="session",
                        workspace_id=ws_id, session_id=sess.session_id)

# Agent scope — crosses workspace boundaries
await client.state_set("agent.notes", "remember X", scope="agent", agent_id="agent-001")

# Scope auto-resolution: agent → session → global
entry = await client.state_get("config.model", workspace_id=ws_id, session_id=sess.session_id)

# Optimistic concurrency
result = await client.state_set("counter", 2, scope="global", workspace_id=ws_id,
                                 if_version=result.version)  # fails with CONFLICT if stale

# TTL
await client.state_set("temp.flag", True, scope="session",
                        workspace_id=ws_id, session_id=sess.session_id,
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
    message={"task": "deploy to staging", "approved": True},
)

# Create a checkpoint
ckpt = await client.session_checkpoint(sess.session_id, label="before-deploy")

# Restore from checkpoint (creates a new session with copied state)
restored = await client.session_restore(ckpt.checkpoint_id)

# Close
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
sub = await client.events_subscribe(ws.workspace_id, since="2026-05-01T00:00:00Z")

# Events are delivered as MCP notifications (see docs/usage.md for handling)
await client.events_unsubscribe(sub.subscription_id)
```

---

## Configuration reference

All configuration is via environment variables. Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `OCP_API_KEYS` | *(unset)* | Comma-separated Bearer tokens. Unset = open/dev mode. |
| `OCP_API_KEY` | *(unset)* | The key the **client** presents (stdio mode). |
| `OCP_API_KEY_WORKSPACES` | *(unset)* | Per-key workspace restrictions: `key1:ws_id1,ws_id2;key2:ws_id3` |
| `OCP_DATABASE_URL` | *(unset)* | PostgreSQL URL. Unset = SQLite. |
| `OCP_DB_PATH` | `ocp.db` | SQLite file path (ignored when DATABASE_URL is set). |
| `OCP_HOST` | `0.0.0.0` | HTTP server bind address. |
| `OCP_PORT` | `8080` | HTTP server port. |
| `OCP_EMBEDDER` | `hash` | Embedding backend: `hash`, `fastembed`, or `openai`. |
| `OCP_EMBED_MODEL` | *(backend default)* | Override the embedding model name. |
| `OCP_EMBED_DIM` | `512` | Hash embedder vector dimension. |
| `OCP_WATCH` | `1` | File watcher (`0` to disable — recommended in tests). |

### Embedding backends

| Backend | `OCP_EMBEDDER` value | Extra dependencies | Quality |
|---|---|---|---|
| Hash n-gram (built-in) | `hash` (default) | none | keyword-level |
| FastEmbed (BAAI/bge) | `fastembed` | `pip install fastembed` | semantic |
| OpenAI | `openai` | `pip install openai` + `OPENAI_API_KEY` | semantic |

---

## Deployment

### Docker

```bash
docker pull ghcr.io/rajesh1213/ocp:latest
docker run -p 8080:8080 ghcr.io/rajesh1213/ocp:latest
```

### Docker Compose (PostgreSQL + OCP server)

```bash
cp .env.example .env
# Edit .env: set OCP_API_KEYS and optionally OCP_EMBEDDER

docker compose up
# OCP server:  http://localhost:8080
# Health:      http://localhost:8080/health
# PostgreSQL:  localhost:5432
```

The compose file starts:
- `pgvector/pgvector:pg16` with a persistent volume
- OCP HTTP server connected to Postgres, with health monitoring

### Production checklist

- Set `OCP_API_KEYS` to a long random token (`openssl rand -hex 32`)
- Use `OCP_DATABASE_URL` pointing to a managed PostgreSQL + pgvector instance
- Set `OCP_EMBEDDER=fastembed` or `openai` for semantic-quality retrieval
- Put the HTTP server behind a TLS-terminating reverse proxy
- Set `OCP_WATCH=0` if the server does not have access to the filesystem being indexed

---

## Testing & conformance

```bash
# Full test suite
pytest packages/ocp-conformance/ -v

# OCP-0002 conformance pack against the reference server
bash scripts/run-conformance.sh

# Specific conformance level
OCP_LEVEL=core bash scripts/run-conformance.sh
OCP_LEVEL=coordination bash scripts/run-conformance.sh

# Against a third-party OCP server
OCP_SERVER_CMD=/path/to/your-ocp-server bash scripts/run-conformance.sh

# Linting
ruff check packages/

# Type checking
mypy packages/ocp-server/ocp_server packages/ocp-client/ocp_client

# Speed up tests (disable file watcher, use hash embedder)
OCP_WATCH=0 OCP_EMBEDDER=hash pytest packages/ocp-conformance/
```

Conformance levels:

| Level | Tools covered |
|---|---|
| `core` | workspace, retrieval, state, scope resolution, invalidation |
| `core+coordination` | core + session coordination |
| `full` | core+coordination + events |

The reference server currently advertises **`core+coordination`** (44/44 tests passing).

---

## Repository layout

```
ocp/
├── docs/
│   ├── integrations.md      # Claude Code, Claude Desktop, Cursor, HTTP/SSE
│   └── usage.md             # Detailed usage guide (this file's companion)
├── spec/
│   ├── OCP-0001.md          # Protocol specification RFC v0.1
│   └── LICENSE.md           # CC-BY 4.0 (specification text)
├── packages/
│   ├── ocp-server/          # Reference server implementation
│   │   └── ocp_server/
│   │       ├── server.py        # MCP wiring, tool dispatch
│   │       ├── server_http.py   # HTTP/SSE transport
│   │       ├── models.py        # Core data model (Chunk, StateEntry, …)
│   │       ├── auth.py          # Bearer token auth, workspace isolation
│   │       ├── embedder.py      # Hash / FastEmbed / OpenAI backends
│   │       ├── indexer.py       # Filesystem workspace indexer
│   │       ├── tools/           # Tool implementations (§4.1–4.5)
│   │       └── storage/         # SQLite + Postgres backends
│   ├── ocp-client/          # Python async client SDK
│   │   └── ocp_client/
│   │       ├── client.py        # OCPClient
│   │       └── types.py         # Pydantic response types
│   └── ocp-conformance/     # OCP-0002 conformance test suite
│       └── ocp_conformance/
│           ├── runner.py        # CLI entry point
│           └── suite/           # 44 test functions across 6 files
├── scripts/
│   ├── dev-install.sh       # One-shot development setup
│   └── run-conformance.sh   # Conformance runner
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── pyproject.toml           # uv workspace root
```

---

## Specification

The protocol specification lives in [`spec/OCP-0001.md`](spec/OCP-0001.md). Key sections:

| Section | Topic |
|---|---|
| §3 | Core data model (Chunk, StateEntry, identity rules, chunk ID stability) |
| §4 | Tool surface (workspace, retrieval, state, coordination, events) |
| §5 | Scope resolution semantics |
| §6 | Invalidation contract |
| §7 | Event subscription model |
| §8 | Security considerations |
| §10 | Conformance levels |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide. Short version:

1. Fork → feature branch → PR against `main`
2. Sign off every commit: `git commit -s`
3. Follow [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`
4. The conformance pack must pass: `bash scripts/run-conformance.sh`
5. Normative spec changes require a Discussion-first process and two maintainer approvals

Issues: [github.com/Rajesh1213/OCP/issues](https://github.com/Rajesh1213/OCP/issues)

---

**License:** Code under [Apache-2.0](LICENSE) · Specification text under [CC-BY 4.0](spec/LICENSE.md)
