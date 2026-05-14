"""Task classifier — scores prompt complexity and derives a routing decision.

Uses tiktoken (cl100k_base, same as ocp-server) for accurate token counting.
All scoring is deterministic heuristics — no model required.

Env vars:
  OCP_ROUTE_THRESHOLD  — complexity score at or above which we route to paid (default: 0.5)
"""
from __future__ import annotations

import os
import re
from typing import Any

from ocp_router.backends.base import ClassifyResult, RouteTarget, TaskType

# ------------------------------------------------------------------ #
# Keyword tables                                                       #
# ------------------------------------------------------------------ #

# Task-type keyword sets — matched case-insensitively against the prompt.
# Order matters: first match wins for task_type; all matches contribute score.
_TASK_KEYWORDS: list[tuple[TaskType, list[str]]] = [
    ("architect", [
        "architecture", "system design", "redesign", "rethink", "restructure",
        "design pattern", "trade.?off", "scalab", "microservice",
    ]),
    ("refactor", [
        "refactor", "rewrite", "clean up", "clean-up", "modularise", "modularize",
        "extract", "decouple", "abstract", "reorganise", "reorganize",
    ]),
    ("debug", [
        "debug", "broken", "not working", "fix the bug", "stack trace",
        "exception", "traceback", "error:", "raises", "fails with",
    ]),
    ("summarise", [
        "summari[sz]e", "summarise", "tldr", "tl;dr", "overview", "brief",
        "what does .* do", "in one sentence",
    ]),
    ("explain", [
        "explain", "what is", "what does", "how does", "walk me through",
        "describe", "clarify",
    ]),
    ("retrieval", [
        "find", "search", "look for", "where is", "which file", "locate",
        "list all", "show me", "grep",
    ]),
]

# Keywords that add complexity score regardless of task type.
# High-risk categories (security, architecture) individually push past the 0.5 threshold.
# Medium-risk categories (refactor, migrate) need 2+ signals to reach paid routing.
_COMPLEX_SIGNALS: list[tuple[str, float, str]] = [
    # (pattern, score_addition, signal_label)
    (r"\bsecurit(y|ies)\b",                    0.55, "security-sensitive"),
    (r"\barchitect",                            0.55, "architecture task"),
    (r"\bdeadlock\b|\brace condition\b",        0.40, "concurrency issue"),
    (r"\bacross\b.{0,40}\bfiles?\b",            0.35, "multi-file scope"),
    (r"\bmulti.?file\b",                        0.35, "multi-file scope"),
    (r"\brefactor\b",                           0.25, "refactor task"),
    (r"\bmigrat(e|ion)\b",                      0.55, "migration task"),
    (r"\bperformance\b|\boptimi[sz]",           0.20, "optimisation task"),
    (r"\b(all|every|each)\b.{0,30}\bfile",      0.20, "bulk-file operation"),
    (r"\bproduction\b|\bdeploy\b",              0.20, "production context"),
]

_SIMPLE_SIGNALS: list[tuple[str, float, str]] = [
    (r"\bsummar[iy]",                           0.10, "summarise task"),
    (r"\bexplain\b",                            0.10, "explain task"),
    (r"\bwhat (is|does|are)\b",                 0.10, "simple question"),
    (r"\bone (line|sentence|word)\b",           0.15, "short output requested"),
    (r"\bsearch\b|\bfind\b|\bgrep\b",           0.10, "retrieval task"),
]

# ------------------------------------------------------------------ #
# Token counting (mirrors ocp-server's Tokenizer)                     #
# ------------------------------------------------------------------ #

class _Tokenizer:
    def __init__(self) -> None:
        self._enc: Any = None

    def _load(self) -> None:
        if self._enc is not None:
            return
        import tiktoken
        self._enc = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        self._load()
        return len(self._enc.encode(text))


_tokenizer = _Tokenizer()


# ------------------------------------------------------------------ #
# Classifier                                                          #
# ------------------------------------------------------------------ #

class TaskClassifier:
    """Scores prompt complexity and decides local vs paid routing.

    All logic is pure heuristics — deterministic, no network calls.
    """

    def __init__(self, threshold: float | None = None) -> None:
        self._threshold = threshold if threshold is not None else float(
            os.environ.get("OCP_ROUTE_THRESHOLD", "0.5")
        )

    # ---------------------------------------------------------------- #
    # Public API                                                        #
    # ---------------------------------------------------------------- #

    def classify(self, prompt: str) -> ClassifyResult:
        """Score *prompt* and return a ClassifyResult."""
        signals: list[str] = []
        score: float = 0.0

        # 1. Token-length signal
        length_score, length_signal = self._length_signal(prompt)
        score += length_score
        if length_signal:
            signals.append(length_signal)

        # 2. Code block signal
        block_score, block_signal = self._code_block_signal(prompt)
        score += block_score
        if block_signal:
            signals.append(block_signal)

        # 3. Complex keyword signals
        for pattern, weight, label in _COMPLEX_SIGNALS:
            if re.search(pattern, prompt, re.IGNORECASE) and label not in signals:
                score += weight
                signals.append(label)

        # 4. Simple keyword signals (reduce score)
        for pattern, weight, label in _SIMPLE_SIGNALS:
            if re.search(pattern, prompt, re.IGNORECASE) and label not in signals:
                score -= weight
                signals.append(f"simple:{label}")

        # 5. Multi-file reference count
        file_score, file_signal = self._multi_file_signal(prompt)
        score += file_score
        if file_signal:
            signals.append(file_signal)

        score = max(0.0, min(1.0, score))

        return ClassifyResult(
            complexity_score=round(score, 3),
            task_type=self._detect_task_type(prompt),
            signals=signals,
            route_to=self._route(score),
        )

    # ---------------------------------------------------------------- #
    # Heuristic helpers                                                 #
    # ---------------------------------------------------------------- #

    def _length_signal(self, prompt: str) -> tuple[float, str]:
        tokens = _tokenizer.count(prompt)
        if tokens < 150:
            return 0.0, ""
        if tokens < 400:
            return 0.10, "medium-prompt(150-400t)"
        if tokens < 800:
            return 0.20, "long-prompt(400-800t)"
        return 0.30, "very-long-prompt(>800t)"

    def _code_block_signal(self, prompt: str) -> tuple[float, str]:
        blocks = re.findall(r"```[\s\S]*?```", prompt)
        if not blocks:
            return 0.0, ""
        total_lines = sum(b.count("\n") for b in blocks)
        if total_lines < 20:
            return 0.05, f"code-block({total_lines}lines)"
        if total_lines < 60:
            return 0.15, f"large-code-block({total_lines}lines)"
        return 0.25, f"very-large-code-block({total_lines}lines)"

    def _multi_file_signal(self, prompt: str) -> tuple[float, str]:
        # Count distinct file-like tokens (path/file.ext patterns)
        files = re.findall(r"[\w/\-]+\.\w{1,6}", prompt)
        distinct = len(set(files))
        if distinct >= 5:
            return 0.20, f"references-{distinct}-files"
        if distinct >= 3:
            return 0.10, f"references-{distinct}-files"
        return 0.0, ""

    def _detect_task_type(self, prompt: str) -> TaskType:
        for task_type, patterns in _TASK_KEYWORDS:
            for pattern in patterns:
                if re.search(pattern, prompt, re.IGNORECASE):
                    return task_type
        return "unknown"

    def _route(self, score: float) -> RouteTarget:
        return "paid" if score >= self._threshold else "local"
