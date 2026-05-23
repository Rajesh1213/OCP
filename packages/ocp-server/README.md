# ocp-server

Reference server implementation for the [Open Context Protocol (OCP)](https://github.com/Rajesh1213/OCP) — a vendor-neutral protocol for sharing retrievable context, persistent state, and invalidation events across AI agents, models, and frameworks.

Every OCP server is an MCP server. Drop it into any MCP-compatible IDE or agent runtime with a single config line.

## What it does

- **Semantic retrieval** — index any codebase or document set, run k-NN search over embedded chunks, assemble token-budget-aware context bundles ready for LLM prompts
- **Scoped state** — typed key-value store with `agent`, `session`, and `global` scopes and optimistic concurrency control
- **Session coordination** — agent handoff, named checkpoints, restore from checkpoint
- **Invalidation & events** — file watcher marks stale chunks automatically; real-time event subscriptions with replay
- **Prompt optimization** — `prompt.prepare` compresses raw prompts using a local SLM before they reach a paid model; `prompt.record_result` logs the output back to accumulate a fine-tuning dataset

## Quick start

```bash
# Zero-install — run directly from PyPI
uvx ocp-server

# Or install permanently
pip install ocp-server
ocp-server          # stdio transport (for IDE / agent integrations)
ocp-server-http     # HTTP/SSE transport (for multi-client / remote scenarios)
```

## IDE integrations

Add to `.mcp.json` in your project root (Claude Code, VS Code, JetBrains):

```json
{
  "mcpServers": {
    "ocp": {
      "command": "uvx",
      "args": ["ocp-server"],
      "env": { "OCP_DB_PATH": "${workspaceFolder}/.ocp.db" }
    }
  }
}
```

Works the same way for **Claude Desktop**, **Cursor**, and any other MCP-compatible runtime. See the [integrations guide](https://github.com/Rajesh1213/OCP/blob/main/docs/integrations.md) for copy-paste configs.

## Storage backends

| Backend | How to enable |
|---|---|
| SQLite (default) | Zero config — just run `ocp-server` |
| PostgreSQL + pgvector | Set `OCP_DATABASE_URL=postgresql://...` |

## Embedding backends

| Backend | `OCP_EMBEDDER` | Extra deps | Quality |
|---|---|---|---|
| Hash n-gram (default) | `hash` | none | keyword-level |
| FastEmbed (BAAI/bge) | `fastembed` | `pip install fastembed` | semantic |
| OpenAI | `openai` | `pip install openai` | semantic |

## Docker

```bash
docker pull ghcr.io/rajesh1213/ocp:latest

# Run with SQLite
docker run -p 8080:8080 -e OCP_API_KEYS=my-key ghcr.io/rajesh1213/ocp:latest

# Run with PostgreSQL
docker run -p 8080:8080 \
  -e OCP_DATABASE_URL=postgresql://user:pass@host/db \
  -e OCP_API_KEYS=my-key \
  ghcr.io/rajesh1213/ocp:latest
```

## Configuration

All configuration via environment variables:

| Variable | Default | Description |
|---|---|---|
| `OCP_DB_PATH` | `ocp.db` | SQLite file path |
| `OCP_DATABASE_URL` | — | PostgreSQL DSN (enables Postgres backend) |
| `OCP_API_KEYS` | — | Comma-separated Bearer tokens (empty = open/dev mode) |
| `OCP_API_KEY` | — | Key the client presents in stdio mode |
| `OCP_HOST` | `0.0.0.0` | HTTP server bind address |
| `OCP_PORT` | `8080` | HTTP server port |
| `OCP_EMBEDDER` | `hash` | Embedding backend: `hash`, `fastembed`, `openai` |
| `OCP_WATCH` | `1` | Set to `0` to disable file watching |

## Conformance

The reference server passes all 44 OCP-0002 conformance tests at the `core+coordination` level.

```bash
# Run conformance suite against this server
pip install ocp-conformance
ocp-conformance
```

## Prompt optimization

Requires `ocp-server[router]` and a running Ollama instance.

```python
# Compress a verbose prompt before sending to a paid model
result = await client.call_tool("prompt.prepare", {
    "prompt": "your raw prompt here",
    "workspace_id": ws_id,    # optional — injects relevant code context
    "session_id": sess_id,    # optional — injects session history
    "budget_tokens": 800,
})
# result["optimized_prompt"]  → compressed, model-ready
# result["trace_id"]          → use with prompt.record_result

# Log the paid-model output back (builds fine-tuning dataset)
await client.call_tool("prompt.record_result", {
    "trace_id": result["trace_id"],
    "result": paid_model_output,
})
```

If Ollama is unavailable, the tool returns the original prompt unchanged (`"optimized": false`).

## Links

- [GitHub](https://github.com/Rajesh1213/OCP)
- [Protocol spec (OCP-0001)](https://github.com/Rajesh1213/OCP/blob/main/spec/OCP-0001.md)
- [Usage guide](https://github.com/Rajesh1213/OCP/blob/main/docs/usage.md)
- [Integrations](https://github.com/Rajesh1213/OCP/blob/main/docs/integrations.md)
- [ocp-client](https://pypi.org/project/ocp-client/) — Python async client SDK

## License

Apache-2.0 — see [LICENSE](https://github.com/Rajesh1213/OCP/blob/main/LICENSE)
