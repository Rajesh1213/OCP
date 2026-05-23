"""prompt.prepare and prompt.record_result tools."""
from __future__ import annotations

import importlib.util
import uuid
from typing import Any

from ocp_server.storage.base import BaseStore

_OPTIMIZER_SYSTEM = """\
You are a prompt optimizer for AI language models.
Rewrite the raw prompt below into a clean, compressed version that:
1. Preserves ALL requirements, constraints, and intent
2. Removes filler words, repetition, and irrelevant context
3. Is direct and specific
4. Stays under {budget_tokens} tokens
5. Is formatted for the {target_model} model family

Output ONLY the optimized prompt — no preamble, no explanation.\
"""

_OPTIMIZER_USER = """\
RAW PROMPT:
{raw_prompt}
{context_section}\
{history_section}\
"""


async def _local_backend() -> Any | None:
    """Return an available OllamaBackend, or None if ocp-router isn't installed."""
    if importlib.util.find_spec("ocp_router") is None:
        return None
    try:
        from ocp_router.backends.ollama import OllamaBackend  # type: ignore[import]
        backend = OllamaBackend()
        if await backend.is_available():
            return backend
    except Exception:
        pass
    return None


def _count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return len(text.split())


async def _build_context_section(
    store: BaseStore,
    embedder: Any,
    tokenizer: Any,
    workspace_id: str,
    raw_prompt: str,
    max_tokens: int,
) -> str:
    query_embedding = await embedder.embed(raw_prompt)
    results = await store.search_chunks(workspace_id, query_embedding, 10, None)
    parts: list[str] = []
    used = 0
    for chunk, _ in results:
        snippet = f"[{chunk.source.uri}]\n{chunk.content}"
        t = tokenizer.count(snippet)
        if used + t > max_tokens:
            break
        parts.append(snippet)
        used += t
    if not parts:
        return ""
    return "\n\nRELEVANT CONTEXT:\n" + "\n\n".join(parts)


async def _build_history_section(
    store: BaseStore,
    tokenizer: Any,
    session_id: str,
    max_tokens: int,
) -> str:
    entries, _ = await store.state_list(None, None, None, session_id, None, None)
    if not entries:
        return ""
    parts: list[str] = []
    used = 0
    for entry in reversed(entries):
        line = f"{entry.key}: {entry.value}"
        t = tokenizer.count(line)
        if used + t > max_tokens:
            break
        parts.append(line)
        used += t
    if not parts:
        return ""
    return "\n\nSESSION HISTORY:\n" + "\n".join(reversed(parts))


async def prompt_prepare(
    store: BaseStore,
    embedder: Any,
    tokenizer: Any,
    prompt: str,
    workspace_id: str | None,
    session_id: str | None,
    target_model: str,
    budget_tokens: int,
) -> dict:
    from ocp_server.storage.sqlite import _now

    context_section = ""
    if workspace_id and await store.workspace_exists(workspace_id):
        context_section = await _build_context_section(
            store, embedder, tokenizer, workspace_id, prompt, max_tokens=400
        )

    history_section = ""
    if session_id:
        history_section = await _build_history_section(
            store, tokenizer, session_id, max_tokens=200
        )

    raw_tokens = _count_tokens(prompt)
    backend = await _local_backend()

    if backend is None:
        trace_id = str(uuid.uuid4())
        await store.save_prompt_trace({
            "trace_id": trace_id,
            "created_at": _now(),
            "raw_prompt": prompt,
            "optimized_prompt": prompt,
            "target_model": target_model,
            "workspace_id": workspace_id,
            "session_id": session_id,
            "raw_tokens": raw_tokens,
            "optimized_tokens": raw_tokens,
        })
        return {
            "trace_id": trace_id,
            "optimized_prompt": prompt,
            "optimized": False,
            "reason": "local_backend_unavailable",
            "original_tokens": raw_tokens,
            "optimized_tokens": raw_tokens,
            "compression_ratio": 1.0,
        }

    from ocp_router.backends.base import GenerateRequest  # type: ignore[import]

    system = _OPTIMIZER_SYSTEM.format(
        budget_tokens=budget_tokens,
        target_model=target_model,
    )
    user_msg = _OPTIMIZER_USER.format(
        raw_prompt=prompt,
        context_section=context_section,
        history_section=history_section,
    )

    response = await backend.generate(
        GenerateRequest(
            prompt=user_msg,
            system=system,
            max_tokens=budget_tokens + 50,
            temperature=0.1,
        )
    )
    optimized = response.text.strip()
    optimized_tokens = _count_tokens(optimized)
    ratio = round(raw_tokens / max(optimized_tokens, 1), 2)

    trace_id = str(uuid.uuid4())
    await store.save_prompt_trace({
        "trace_id": trace_id,
        "created_at": _now(),
        "raw_prompt": prompt,
        "optimized_prompt": optimized,
        "target_model": target_model,
        "workspace_id": workspace_id,
        "session_id": session_id,
        "raw_tokens": raw_tokens,
        "optimized_tokens": optimized_tokens,
    })

    return {
        "trace_id": trace_id,
        "optimized_prompt": optimized,
        "optimized": True,
        "original_tokens": raw_tokens,
        "optimized_tokens": optimized_tokens,
        "compression_ratio": ratio,
    }


async def prompt_record_result(
    store: BaseStore,
    trace_id: str,
    result: str,
) -> dict:
    recorded = await store.record_prompt_result(trace_id, result)
    if not recorded:
        raise TraceNotFoundError(trace_id)
    return {"recorded": True, "trace_id": trace_id}


class TraceNotFoundError(Exception):
    code = "TRACE_NOT_FOUND"

    def __init__(self, trace_id: str) -> None:
        super().__init__(f"Prompt trace not found: {trace_id}")
