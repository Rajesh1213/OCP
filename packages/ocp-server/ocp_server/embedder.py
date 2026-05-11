"""Embedding backends for OCP.

Default: HashEmbedder — pure numpy, zero dependencies, works on any platform/Python.
Optional backends (set OCP_EMBEDDER env var):
  - "hash"      HashEmbedder (default)
  - "fastembed" FastEmbedEmbedder (requires: pip install fastembed)
  - "openai"    OpenAIEmbedder    (requires: pip install openai, OPENAI_API_KEY set)
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbedderProtocol(Protocol):
    async def embed(self, text: str) -> list[float]: ...
    @property
    def dim(self) -> int: ...


# ------------------------------------------------------------------ #
# Hash n-gram embedder (built-in, no extra deps)                      #
# ------------------------------------------------------------------ #

class HashEmbedder:
    """
    Locality-sensitive hash embedding via character n-grams.

    Splits text into overlapping 3-grams, hashes each into one of `dim`
    buckets, and returns an L2-normalised count vector.  Fast, deterministic,
    works on Python 3.13 + Intel Mac with only numpy.
    """

    def __init__(self, dim: int = 512) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, text: str) -> list[float]:
        return self._embed_sync(text)

    def _embed_sync(self, text: str) -> list[float]:
        import numpy as np

        text = re.sub(r"\s+", " ", text.lower()).strip()
        vec = np.zeros(self._dim, dtype=np.float32)

        # Character 3-grams
        for i in range(len(text) - 2):
            gram = text[i : i + 3]
            bucket = int(hashlib.md5(gram.encode()).hexdigest(), 16) % self._dim
            vec[bucket] += 1.0

        # Word unigrams (extra signal for exact-keyword matches)
        for word in text.split():
            bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % self._dim
            vec[bucket] += 2.0

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec.tolist()


# ------------------------------------------------------------------ #
# fastembed backend (optional)                                         #
# ------------------------------------------------------------------ #

class FastEmbedEmbedder:
    """Uses fastembed (BAAI/bge-small-en-v1.5 by default)."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model_name = model_name
        self._model = None
        self._dim_val: int | None = None

    @property
    def dim(self) -> int:
        return self._dim_val or 384

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
        vec = result[0].tolist()
        self._dim_val = len(vec)
        return vec


# ------------------------------------------------------------------ #
# OpenAI backend (optional)                                            #
# ------------------------------------------------------------------ #

class OpenAIEmbedder:
    """Uses the OpenAI embeddings API (text-embedding-3-small)."""

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        self._model = model

    @property
    def dim(self) -> int:
        return 1536

    async def embed(self, text: str) -> list[float]:
        import openai
        client = openai.AsyncOpenAI()
        resp = await client.embeddings.create(input=text, model=self._model)
        return resp.data[0].embedding


# ------------------------------------------------------------------ #
# Factory                                                              #
# ------------------------------------------------------------------ #

def make_embedder() -> EmbedderProtocol:
    backend = os.environ.get("OCP_EMBEDDER", "hash").lower()
    if backend == "fastembed":
        model = os.environ.get("OCP_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
        return FastEmbedEmbedder(model)
    if backend == "openai":
        model = os.environ.get("OCP_EMBED_MODEL", "text-embedding-3-small")
        return OpenAIEmbedder(model)
    return HashEmbedder(dim=int(os.environ.get("OCP_EMBED_DIM", "512")))


# Convenience alias used by server.py
Embedder = HashEmbedder


# ------------------------------------------------------------------ #
# Tokenizer                                                            #
# ------------------------------------------------------------------ #

class Tokenizer:
    """Token counter.  Uses tiktoken when available, falls back to word-split."""

    def __init__(self) -> None:
        self._enc = None
        self._use_tiktoken = True

    def _load(self) -> None:
        if self._enc is not None or not self._use_tiktoken:
            return
        try:
            import tiktoken
            self._enc = tiktoken.get_encoding("cl100k_base")
        except ImportError:
            self._use_tiktoken = False

    def count(self, text: str) -> int:
        self._load()
        if self._enc:
            return len(self._enc.encode(text))
        # Fallback: ~4 chars per token heuristic
        return max(1, len(text) // 4)
