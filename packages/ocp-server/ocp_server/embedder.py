"""Async embedding wrapper around fastembed."""
from __future__ import annotations

import asyncio
from functools import lru_cache


class Embedder:

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model_name = model_name
        self._model = None

    def _load(self) -> None:
        if self._model is None:
            from fastembed import TextEmbedding
            self._model = TextEmbedding(model_name=self._model_name)

    async def embed(self, text: str) -> list[float]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._embed_sync, text)

    def _embed_sync(self, text: str) -> list[float]:
        self._load()
        result = list(self._model.embed([text]))
        return result[0].tolist()


class Tokenizer:
    """Rough token counter using tiktoken cl100k_base."""

    def __init__(self) -> None:
        self._enc = None

    def _load(self) -> None:
        if self._enc is None:
            import tiktoken
            self._enc = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        self._load()
        return len(self._enc.encode(text))
