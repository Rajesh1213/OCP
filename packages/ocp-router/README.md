# ocp-router

Hybrid local/cloud model routing layer for [Open Context Protocol](https://github.com/Rajesh1213/OCP).

Routes simple context tasks to a local model (Ollama), escalating complex reasoning to paid providers — reducing cost, latency, and token usage.

---

## Installation

```bash
pip install ocp-router
```

Requires Python 3.11+ and a running [Ollama](https://ollama.com) instance for local model support.

---

## Quick start

```python
import asyncio
from ocp_router import make_local_backend, GenerateRequest

async def main():
    backend = make_local_backend()          # reads env vars, defaults to Ollama + llama3.2

    # Check the local model is ready
    if not await backend.is_available():
        print("Ollama not running or model not pulled — run: ollama pull llama3.2")
        return

    # Plain text generation
    req = GenerateRequest(
        prompt="Summarise this function in one sentence: def add(a, b): return a + b",
        max_tokens=64,
        temperature=0.2,
    )
    resp = await backend.generate(req)
    print(resp.text)
    print(f"tokens: {resp.prompt_tokens} in / {resp.completion_tokens} out  ({resp.duration_ms:.0f}ms)")

asyncio.run(main())
```

---

## Chat messages

Pass a `messages` list to use the `/api/chat` endpoint instead of `/api/generate`:

```python
req = GenerateRequest(
    prompt="",
    system="You are a senior Python engineer. Be concise.",
    messages=[
        {"role": "user", "content": "What does __slots__ do?"},
    ],
)
resp = await backend.generate(req)
print(resp.text)
```

---

## Configuration

All options are set via environment variables — no code changes needed.

| Variable | Default | Description |
|---|---|---|
| `OCP_LOCAL_BACKEND` | `ollama` | Backend type. Only `ollama` supported today. |
| `OCP_LOCAL_MODEL` | `llama3.2` | Model name passed to Ollama. |
| `OCP_OLLAMA_URL` | `http://localhost:11434` | Ollama base URL. Point to a remote GPU box if needed. |
| `OCP_LOCAL_TIMEOUT` | `60` | Inference timeout in seconds. |

```bash
# Example: use Mistral on a remote GPU server
OCP_LOCAL_MODEL=mistral OCP_OLLAMA_URL=http://gpu-box:11434 python my_agent.py
```

---

## Supported local models

Any model available in Ollama works. Recommended starting points:

| Model | Size | Good for |
|---|---|---|
| `llama3.2` | 2B | Classification, summarisation, prompt prep |
| `phi4-mini` | 3.8B | Code explanation, simple Q&A |
| `mistral` | 7B | Context compression, draft generation |
| `codellama` | 7B | Code-specific tasks |

Pull a model before use:

```bash
ollama pull llama3.2
```

---

## Adding a custom backend

Implement the `LocalModelBackend` protocol to plug in any inference runtime (llama.cpp, vLLM, etc.):

```python
from ocp_router.backends.base import LocalModelBackend, GenerateRequest, GenerateResponse

class MyBackend:
    @property
    def model(self) -> str:
        return "my-model"

    async def is_available(self) -> bool:
        ...

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        ...

# Use directly — no factory change needed
backend: LocalModelBackend = MyBackend()
```

---

## Running tests

```bash
# Unit tests (no Ollama required)
pytest packages/ocp-router/tests/ -k "not integration"

# Integration test (requires ollama serve + ollama pull llama3.2)
pytest packages/ocp-router/tests/ -m integration -v
```

---

## What's next

- `ocp.task.classify` — score request complexity (0.0–1.0) using heuristics + embeddings
- `ocp.model.route` — decide local vs paid based on complexity score
- `ocp.prompt.prepare` — SLM compresses and optimises prompts before they reach the paid model
