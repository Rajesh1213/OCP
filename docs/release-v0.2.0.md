# OCP v0.2.0 — Hybrid Routing

## What's new

This release introduces `ocp-router` — a hybrid local/cloud model routing layer that sits between your agent and your AI providers.

Instead of sending every request to a paid API, OCP now scores each request for complexity and dispatches it to the right model tier automatically:

- **Simple tasks** (explain, search, summarise) → local model via Ollama — fast, free, private
- **Complex tasks** (security review, architecture, multi-file refactor) → your paid provider

Your IDE workflow is unchanged. Simple questions get faster answers. Your API bill shrinks.

---

## New package: `ocp-router`

```bash
pip install ocp-router                  # core — Ollama local backend included
pip install ocp-router[anthropic]       # + Anthropic Claude paid backend
pip install ocp-router[openai]          # + OpenAI paid backend
```

---

## How it works

### 1. TaskClassifier — instant complexity scoring

Every request is scored `0.0 → 1.0` by `TaskClassifier` using deterministic heuristics — no model required, runs in microseconds:

```
"explain this function"                     → 0.00  local
"find all usages of db.connect"             → 0.00  local
"summarise the last session"                → 0.00  local
"refactor the login function"               → 0.25  local
──────────────────────────── threshold 0.5 ─────────────────
"review security vulnerabilities"           → 0.55  paid
"design the payment architecture"           → 0.55  paid
"debug this production deadlock"            → 0.60  paid
"migrate the database schema"               → 0.55  paid
"refactor auth across all files"            → 0.80  paid
```

Scoring layers:
- Token length (tiktoken `cl100k_base` — same encoder as `ocp-server`)
- Code block size (fenced ` ``` ` blocks)
- Complex keyword signals: `security +0.55`, `architect +0.55`, `migrate +0.55`, `deadlock +0.40`, `multi-file +0.35`, `refactor +0.25` ...
- Simple keyword signals: `explain -0.10`, `summarise -0.10`, `search -0.10` ...
- Distinct file path references in the prompt

### 2. OCPRouter — classify → dispatch → trace

```python
from ocp_router import make_router

router = make_router()   # reads all config from env vars

result = await router.route("explain the auth middleware")

result.route_to                    # "local"
result.classify.complexity_score   # 0.00
result.classify.signals            # []
result.model                       # "llama3.2"
result.prompt_tokens               # 34
result.completion_tokens           # 41
result.duration_ms                 # measured on your hardware
result.text                        # the answer
```

Every call returns a `RouteResult` with the answer **and** a full trace of the routing decision — which heuristics fired, the score, and which model was used.

### 3. OllamaBackend — verified locally

Integration-tested against llama3.2 running via `ollama serve`:

```
test_ollama_live_generate  PASSED  (10.59s including model cold start)
```

Local inference speed depends on hardware. Ollama uses Metal GPU acceleration on Apple Silicon and CUDA on NVIDIA GPUs — a dedicated GPU host is recommended for production.

### 4. Vendor-neutral paid backend

Both local and paid slots accept any object implementing the `ModelBackend` protocol — three methods, no base class:

```python
class MyBackend:
    @property
    def model(self) -> str: ...
    async def is_available(self) -> bool: ...
    async def generate(self, request: GenerateRequest) -> GenerateResponse: ...

router = OCPRouter(local=MyBackend(), paid=MyBackend(), classifier=TaskClassifier())
```

`AnthropicBackend` and `OpenAIBackend` are convenience adapters, not required dependencies.

---

## IDE integration

Add three env vars to your `.mcp.json` — no other changes needed:

```json
{
  "mcpServers": {
    "ocp": {
      "command": "uvx",
      "args": ["ocp-server"],
      "env": {
        "OCP_DB_PATH": "${workspaceFolder}/.ocp.db",
        "OCP_LOCAL_MODEL": "llama3.2",
        "OCP_OLLAMA_URL": "http://localhost:11434",
        "OCP_ROUTE_THRESHOLD": "0.5"
      }
    }
  }
}
```

Works with Claude Code, Cursor, Windsurf, and any MCP-compatible IDE.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `OCP_LOCAL_MODEL` | `llama3.2` | Ollama model |
| `OCP_OLLAMA_URL` | `http://localhost:11434` | Ollama base URL |
| `OCP_PAID_BACKEND` | `anthropic` | `anthropic` or `openai` |
| `OCP_PAID_MODEL` | `claude-sonnet-4-6` | Paid model identifier |
| `OCP_ROUTE_THRESHOLD` | `0.5` | Escalation threshold |

---

## What's tested

- 54 unit tests passing (classifier, Ollama backend, router — all mocked)
- 1 integration test passing (live Ollama + llama3.2)
- CI: lint (ruff), type-check (mypy), unit tests, conformance suite — all green

---

## What's next

`ocp.prompt.prepare` — before a complex request reaches your paid provider, a local SLM compresses and optimises the prompt. Fewer tokens in, better answer out.

---

## Upgrading

```bash
pip install --upgrade ocp-router
```

`ocp-server` and `ocp-client` are unchanged at v0.1.1. The router is an independent package — no breaking changes to existing OCP deployments.
