# ocp-router

Hybrid local/cloud model routing layer for [Open Context Protocol](https://github.com/Rajesh1213/OCP).

Scores each request for complexity and dispatches it to the right model tier — local model for simple tasks, paid provider for complex reasoning. Vendor-neutral: works with any backend that implements the `ModelBackend` protocol.

---

## Installation

```bash
pip install ocp-router                   # core — Ollama local backend included
pip install ocp-router[anthropic]        # + Anthropic Claude paid backend
pip install ocp-router[openai]           # + OpenAI paid backend
```

Requires Python 3.11+ and a running [Ollama](https://ollama.com) instance for local model support.

---

## Quick start

```bash
# One-time: install Ollama and pull a model
brew install ollama          # macOS — see ollama.com for other platforms
ollama pull llama3.2
ollama serve
```

```python
import asyncio
from ocp_router import make_router

async def main():
    router = make_router()   # reads all config from env vars

    # Simple request — handled locally, no paid API call
    result = await router.route("explain the verify_token function")
    print(result.route_to)                    # "local"
    print(result.classify.complexity_score)   # 0.0
    print(result.model)                       # "llama3.2"
    print(result.text)

    # Complex request — escalated to paid provider
    result = await router.route(
        "review security vulnerabilities across all API endpoints"
    )
    print(result.route_to)                    # "paid"
    print(result.classify.complexity_score)   # 0.55
    print(result.classify.signals)            # ["security-sensitive"]
    print(result.model)                       # "claude-sonnet-4-6"
    print(result.text)

asyncio.run(main())
```

---

## How routing works

Every request passes through the `TaskClassifier` before reaching any model. The classifier scores complexity from `0.0` (trivial) to `1.0` (maximum) using five deterministic heuristic layers — no model required, runs in microseconds.

```
Request prompt
      │
      ▼
┌─────────────────────────────────────────────┐
│  TaskClassifier                             │
│                                             │
│  1. Token length      (tiktoken cl100k)     │
│  2. Code block size   (fenced ``` blocks)   │
│  3. Complex signals   security +0.55        │
│                       architecture +0.55    │
│                       migration +0.55       │
│                       deadlock +0.40        │
│                       multi-file +0.35      │
│                       refactor +0.25  ...   │
│  4. Simple signals    explain -0.10         │
│                       summarise -0.10       │
│                       search -0.10    ...   │
│  5. File references   3-4 files +0.10       │
│                       5+ files  +0.20       │
│                                             │
│  score = clamp(sum, 0.0, 1.0)              │
└──────────────┬──────────────────────────────┘
               │
       ┌───────┴────────┐
  score < 0.5      score ≥ 0.5
       │                │
       ▼                ▼
  Local model      Paid provider
  (Ollama)         (Claude / GPT-4 / any)
```

### What goes where

| Request | Score | Route |
|---|---|---|
| "explain this function" | 0.00 | local |
| "what does add() do?" | 0.00 | local |
| "find all usages of db.connect" | 0.00 | local |
| "summarise the last session" | 0.00 | local |
| "refactor the login function" | 0.25 | local |
| — threshold (default 0.5) — | | |
| "refactor auth across all files" | 0.80 | paid |
| "review security vulnerabilities" | 0.55 | paid |
| "design the payment architecture" | 0.55 | paid |
| "debug this production deadlock" | 0.60 | paid |
| "migrate the database schema" | 0.55 | paid |

Threshold is configurable via `OCP_ROUTE_THRESHOLD`.

---

## RouteResult — what you get back

Every `router.route()` call returns a `RouteResult` with the answer and a full trace of the routing decision:

```python
@dataclass
class RouteResult:
    text: str                  # the model's response
    route_to: str              # "local" or "paid"
    classify: ClassifyResult   # full classification trace
    model: str                 # exact model identifier used
    prompt_tokens: int
    completion_tokens: int
    duration_ms: float

@dataclass
class ClassifyResult:
    complexity_score: float    # 0.0 – 1.0
    task_type: str             # "explain" | "refactor" | "debug" | "architect" | ...
    signals: list[str]         # which heuristics fired
    route_to: str              # "local" or "paid"
```

---

## Configuration

All options are set via environment variables — no code changes needed.

### Local backend

| Variable | Default | Description |
|---|---|---|
| `OCP_LOCAL_BACKEND` | `ollama` | Backend type. `ollama` is the only built-in option. |
| `OCP_LOCAL_MODEL` | `llama3.2` | Model name passed to Ollama. |
| `OCP_OLLAMA_URL` | `http://localhost:11434` | Ollama base URL. Point to a remote GPU box if needed. |
| `OCP_LOCAL_TIMEOUT` | `60` | Inference timeout in seconds. |

### Paid backend

| Variable | Default | Description |
|---|---|---|
| `OCP_PAID_BACKEND` | `anthropic` | Backend type: `anthropic` or `openai`. |
| `OCP_PAID_MODEL` | `claude-sonnet-4-6` | Model identifier for the paid provider. |
| `OCP_PAID_MAX_TOKENS` | `4096` | Max tokens for paid responses. |
| `ANTHROPIC_API_KEY` | *(required)* | API key for Anthropic backend. |
| `OPENAI_API_KEY` | *(required)* | API key for OpenAI backend. |

### Router

| Variable | Default | Description |
|---|---|---|
| `OCP_ROUTE_THRESHOLD` | `0.5` | Complexity score at or above which requests go to paid. |

```bash
# Example: Mistral locally, GPT-4o for complex tasks, stricter threshold
OCP_LOCAL_MODEL=mistral \
OCP_PAID_BACKEND=openai \
OCP_PAID_MODEL=gpt-4o \
OCP_ROUTE_THRESHOLD=0.6 \
python my_agent.py
```

---

## IDE integration (Claude Code, Cursor, Windsurf)

Add the routing env vars to your `.mcp.json` — OCP handles the rest:

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
        "OCP_PAID_BACKEND": "anthropic",
        "OCP_ROUTE_THRESHOLD": "0.5"
      }
    }
  }
}
```

Simple tasks (explain, search, summarise) are answered locally by Ollama. Complex requests (security, architecture, multi-file refactor) escalate to your paid provider. Your IDE workflow is unchanged.

---

## Supported local models

Any model available in Ollama works. Recommended starting points:

| Model | Size | Good for |
|---|---|---|
| `llama3.2` | 2B | Classification, summarisation, simple Q&A |
| `phi4-mini` | 3.8B | Code explanation, short answers |
| `mistral` | 7B | Context compression, draft generation |
| `codellama` | 7B | Code-specific tasks |

```bash
ollama pull llama3.2
```

---

## Bring your own backend

Both the local and paid slots accept any object that implements the `ModelBackend` protocol — three methods, no base class required:

```python
from ocp_router import OCPRouter, TaskClassifier
from ocp_router.backends.base import GenerateRequest, GenerateResponse

class MyVLLMBackend:
    @property
    def model(self) -> str:
        return "mistral-7b-instruct"

    async def is_available(self) -> bool:
        return True   # check your endpoint

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        # call your inference endpoint
        ...
        return GenerateResponse(
            text="...",
            model=self.model,
            prompt_tokens=0,
            completion_tokens=0,
            duration_ms=0.0,
        )

# Plug in directly — no factory change needed
router = OCPRouter(
    local=MyVLLMBackend(),
    paid=MyVLLMBackend(),   # or any other backend
    classifier=TaskClassifier(),
)
```

This is the intended extension point. `ocp-router` ships `OllamaBackend`, `AnthropicBackend`, and `OpenAIBackend` as convenience implementations — not as the only options.

---

## Running tests

```bash
# Unit tests — no Ollama or API keys required
pytest packages/ocp-router/tests/ -k "not integration" -v

# Integration test — requires: ollama serve + ollama pull llama3.2
pytest packages/ocp-router/tests/ -m integration -v
```

---

## What's next

- `ocp.prompt.prepare` — local SLM compresses and optimises prompts before they reach the paid provider, reducing token usage and improving answer quality
