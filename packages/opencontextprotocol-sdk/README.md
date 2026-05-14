# opencontextprotocol-sdk

One-line install for [Open Context Protocol](https://opencontextprotocol.ai).

```bash
pip install opencontextprotocol-sdk              # server + client
pip install opencontextprotocol-sdk[router]      # + hybrid local/cloud routing (Ollama)
pip install opencontextprotocol-sdk[anthropic]   # + Anthropic Claude paid backend
pip install opencontextprotocol-sdk[openai]      # + OpenAI paid backend
pip install opencontextprotocol-sdk[full]        # everything
```

This is a meta-package. It installs and keeps in sync:

| Package | What it provides |
|---|---|
| `ocp-server` | MCP server — runs in your IDE via `.mcp.json` |
| `ocp-client` | Python async client SDK |
| `ocp-router` | Hybrid routing layer (optional) |

## Quick start

```bash
# IDE integration (Claude Code, Cursor, Windsurf)
pip install opencontextprotocol-sdk
uvx ocp-server   # or configure via .mcp.json
```

```bash
# With hybrid routing (local model handles simple tasks)
pip install opencontextprotocol-sdk[router]
ollama pull llama3.2
```

See [opencontextprotocol.ai](https://opencontextprotocol.ai) for full documentation.
