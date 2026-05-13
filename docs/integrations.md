# OCP — Integrations

OCP ships as a standard MCP server. Any MCP-compatible IDE or agent runtime can connect to it — either via stdio (for local use) or HTTP/SSE (for remote/multi-agent use).

---

## Prerequisites

Install the server with [uv](https://docs.astral.sh/uv/):

```bash
pip install ocp-server          # permanent install
# or run without installing:
uvx ocp-server                  # stdio transport
uvx ocp-server ocp-server-http  # HTTP/SSE transport
```

---

## Claude Code (VS Code / JetBrains)

Add to `.mcp.json` in your project root (already present if you cloned this repo):

```json
{
  "mcpServers": {
    "ocp": {
      "command": "uvx",
      "args": ["ocp-server"],
      "env": {
        "OCP_DB_PATH": "${workspaceFolder}/.ocp.db"
      }
    }
  }
}
```

Restart Claude Code after saving. The `mcp__ocp__*` tools will appear automatically.

---

## Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "ocp": {
      "command": "uvx",
      "args": ["ocp-server"],
      "env": {
        "OCP_DB_PATH": "/Users/you/.ocp.db"
      }
    }
  }
}
```

Restart Claude Desktop. OCP tools appear in the tool picker.

**With auth** (recommended for shared machines):

```json
{
  "mcpServers": {
    "ocp": {
      "command": "uvx",
      "args": ["ocp-server"],
      "env": {
        "OCP_DB_PATH": "/Users/you/.ocp.db",
        "OCP_API_KEYS": "my-secret-key",
        "OCP_API_KEY": "my-secret-key"
      }
    }
  }
}
```

---

## Cursor

Add to `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` in a project:

```json
{
  "mcpServers": {
    "ocp": {
      "command": "uvx",
      "args": ["ocp-server"],
      "env": {
        "OCP_DB_PATH": "${workspaceFolder}/.ocp.db"
      }
    }
  }
}
```

---

## Windsurf / other stdio-based runtimes

Any runtime that supports the MCP stdio transport works the same way:

| Config key | Value |
|---|---|
| `command` | `uvx` |
| `args` | `["ocp-server"]` |
| `env.OCP_DB_PATH` | path to a writable `.db` file |

---

## Remote agents — HTTP/SSE transport

For multi-agent setups where agents run in separate processes or containers, use the HTTP server instead of stdio.

**Start the server:**

```bash
OCP_API_KEYS=my-secret-key OCP_PORT=8080 uvx ocp-server -- ocp-server-http
# or via Docker:
docker run -e OCP_API_KEYS=my-secret-key -p 8080:8080 ghcr.io/rajesh1213/ocp:latest
```

**Agent config (SSE transport):**

```json
{
  "mcpServers": {
    "ocp": {
      "url": "http://localhost:8080/sse",
      "headers": {
        "Authorization": "Bearer my-secret-key"
      }
    }
  }
}
```

**Python agent using the client SDK:**

```python
from ocp_client import OCPClient
import asyncio

async def main():
    async with OCPClient.stdio(["uvx", "ocp-server"]) as client:
        ws = await client.workspace_register("file:///path/to/repo")
        await client.workspace_index(ws.workspace_id)
        results = await client.context_search(ws.workspace_id, "authentication middleware")
        for chunk, score in zip(results.chunks, results.scores):
            print(f"[{score:.3f}] {chunk.source.uri}")
            print(chunk.content[:300])

asyncio.run(main())
```

---

## Docker Compose (Postgres backend)

For production use with persistent storage and multiple agents:

```bash
cp .env.example .env        # set OCP_API_KEYS=...
docker compose up -d
```

The server listens on `http://localhost:8080`. Connect agents via the SSE config above.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `OCP_DB_PATH` | `ocp.db` | SQLite database path (ignored when Postgres is set) |
| `OCP_DATABASE_URL` | — | PostgreSQL DSN — enables Postgres backend |
| `OCP_API_KEYS` | — | Comma-separated valid Bearer tokens (empty = open/dev mode) |
| `OCP_API_KEY` | — | Key to authenticate with (stdio mode — set by the spawning process) |
| `OCP_HOST` | `0.0.0.0` | Bind address (HTTP server only) |
| `OCP_PORT` | `8080` | Listen port (HTTP server only) |
| `OCP_EMBEDDER` | `hash` | Embedding backend: `hash` \| `fastembed` \| `openai` |
| `OCP_WATCH` | `1` | Set to `0` to disable file-change watching |
