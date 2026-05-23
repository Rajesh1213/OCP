# ocp-trainer

Fine-tuning pipeline for [Open Context Protocol](https://github.com/Rajesh1213/OCP). Exports prompt traces accumulated by `prompt.prepare` as a training dataset, fine-tunes a local SLM, and registers the result with Ollama — so the prompt optimiser gets smarter over time.

```
prompt.prepare  →  prompt_traces (SQLite)
                        │
                   ocp-trainer stats    ← how much data? token savings?
                   ocp-trainer export   ← JSONL dataset
                   ocp-trainer finetune ← LoRA (local GPU) or OpenAI API
                   ocp-trainer register ← ollama create ocp-optimizer
                        │
                   OCP_LOCAL_MODEL=ocp-optimizer
```

## Installation

```bash
pip install ocp-trainer                  # export + stats only
pip install ocp-trainer[unsloth]         # + local LoRA fine-tuning (GPU required)
pip install ocp-trainer[openai]          # + OpenAI fine-tuning API
```

## Quick start

```bash
# 1. Check how much data you have and token savings so far
ocp-trainer stats --db ocp.db

# 2. Export the dataset (needs >= 100 completed traces for fine-tuning)
ocp-trainer export --db ocp.db --output dataset.jsonl --format alpaca

# 3a. Fine-tune locally (requires GPU + pip install ocp-trainer[unsloth])
ocp-trainer finetune --dataset dataset.jsonl --backend local

# 3b. Fine-tune via OpenAI API (no GPU needed)
ocp-trainer finetune --dataset dataset.jsonl --backend openai --format chatml

# 4. Register fine-tuned model with Ollama (local path only)
ocp-trainer register --adapter ./ocp-optimizer-lora --name ocp-optimizer

# 5. Use the fine-tuned model
OCP_LOCAL_MODEL=ocp-optimizer ocp-server
```

## Commands

### `ocp-trainer stats`

Shows token savings dashboard:

```
Total requests   : 1250
Completed traces : 1100
Tokens (raw)     : 450,000
Tokens (optimised): 185,000
Tokens saved     : 265,000
Avg compression  : 2.43x
Est. cost saved  : $2.6500  (at $0.01/1k tokens)
Fine-tune ready  : yes
```

Set `OCP_COST_PER_1K_TOKENS` to match your actual model pricing.

### `ocp-trainer export`

```bash
ocp-trainer export \
  --db ocp.db \
  --output dataset.jsonl \
  --format alpaca          # alpaca | chatml | openai
  --workspace-id ws_abc    # optional filter
  --since 2026-01-01       # optional date cutoff
```

### `ocp-trainer finetune`

```bash
# Local (GPU required — install ocp-trainer[unsloth])
ocp-trainer finetune \
  --dataset dataset.jsonl \
  --backend local \
  --model unsloth/llama-3.2-3b-instruct \
  --max-steps 200 \
  --output-dir ./ocp-optimizer-lora

# OpenAI API (no GPU — install ocp-trainer[openai])
ocp-trainer finetune \
  --dataset dataset.jsonl \
  --backend openai \
  --model gpt-4o-mini \
  --format chatml
```

### `ocp-trainer register`

Writes an Ollama `Modelfile` and runs `ollama create`:

```bash
ocp-trainer register --adapter ./ocp-optimizer-lora --name ocp-optimizer
# → OCP_LOCAL_MODEL=ocp-optimizer
```

## Links

- [GitHub](https://github.com/Rajesh1213/OCP)
- [ocp-server](https://pypi.org/project/ocp-server/) — prompt.prepare tool
- [opencontextprotocol.ai](https://www.opencontextprotocol.ai)
