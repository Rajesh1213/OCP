"""Tests for TaskClassifier — all deterministic, no network required."""
from __future__ import annotations

import pytest

from ocp_router.classifier import TaskClassifier
from ocp_router.backends.base import ClassifyResult


@pytest.fixture
def clf():
    return TaskClassifier(threshold=0.5)


# ------------------------------------------------------------------ #
# Return type                                                          #
# ------------------------------------------------------------------ #

def test_returns_classify_result(clf):
    result = clf.classify("explain this function")
    assert isinstance(result, ClassifyResult)
    assert 0.0 <= result.complexity_score <= 1.0
    assert result.task_type in {"retrieval", "summarise", "explain", "refactor", "debug", "architect", "unknown"}
    assert result.route_to in {"local", "paid"}
    assert isinstance(result.signals, list)


# ------------------------------------------------------------------ #
# Task type detection                                                  #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("prompt,expected_type", [
    ("explain what this function does",               "explain"),
    ("summarize the last session",                    "summarise"),
    ("search for all usages of db.connect",           "retrieval"),
    ("find where UserService is defined",             "retrieval"),
    ("refactor the auth module to remove duplication","refactor"),
    ("debug why this raises a KeyError",              "debug"),
    ("design the architecture for a new payment service", "architect"),
    ("the stack trace shows a TypeError",             "debug"),
])
def test_task_type_detection(clf, prompt, expected_type):
    result = clf.classify(prompt)
    assert result.task_type == expected_type, (
        f"prompt={prompt!r} → got {result.task_type!r}, expected {expected_type!r}"
    )


# ------------------------------------------------------------------ #
# Simple prompts route locally                                        #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("prompt", [
    "explain this function",
    "what does add() do?",
    "summarise in one sentence",
    "find the login function",
    "search for db.connect",
])
def test_simple_prompts_route_local(clf, prompt):
    result = clf.classify(prompt)
    assert result.route_to == "local", (
        f"prompt={prompt!r} scored {result.complexity_score} — expected local, got paid\nsignals={result.signals}"
    )


# ------------------------------------------------------------------ #
# Complex prompts route to paid                                        #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("prompt", [
    "refactor the entire authentication module across all files to use the new OAuth2 library",
    "design the architecture for a microservice that handles payments at scale",
    "debug this deadlock — here is the stack trace from production: ...",
    "review the security vulnerabilities across all endpoints in the API",
    "migrate the legacy database schema to Postgres across auth.py, models.py, and migrations/",
])
def test_complex_prompts_route_paid(clf, prompt):
    result = clf.classify(prompt)
    assert result.route_to == "paid", (
        f"prompt={prompt!r} scored {result.complexity_score} — expected paid, got local\nsignals={result.signals}"
    )


# ------------------------------------------------------------------ #
# Score ordering — more complex prompt scores higher                  #
# ------------------------------------------------------------------ #

def test_score_ordering():
    clf = TaskClassifier(threshold=0.5)
    simple  = clf.classify("explain this function")
    medium  = clf.classify("refactor the login function to remove duplication")
    complex_ = clf.classify(
        "redesign the architecture of the entire auth system across "
        "auth.py, middleware.py, models.py, and tests/ to support multi-tenant security"
    )
    assert simple.complexity_score < medium.complexity_score < complex_.complexity_score, (
        f"simple={simple.complexity_score}, medium={medium.complexity_score}, complex={complex_.complexity_score}"
    )


# ------------------------------------------------------------------ #
# Token length signal                                                  #
# ------------------------------------------------------------------ #

def test_long_prompt_scores_higher_than_short():
    clf = TaskClassifier(threshold=0.5)
    # Use a refactor prompt so no simple-signal reduction interferes with the length signal
    short  = clf.classify("refactor the login module")
    long_p = clf.classify("refactor the login module " + ("considering all edge cases and existing tests " * 40))
    assert long_p.complexity_score > short.complexity_score


def test_very_long_prompt_signal_present():
    clf = TaskClassifier(threshold=0.5)
    # >800 tokens triggers the very-long-prompt signal
    prompt = "analyse this codebase " + ("token filler word " * 100)
    result = clf.classify(prompt)
    assert any("prompt" in s for s in result.signals)


# ------------------------------------------------------------------ #
# Code block signal                                                    #
# ------------------------------------------------------------------ #

def test_large_code_block_raises_score():
    clf = TaskClassifier(threshold=0.5)
    no_code = clf.classify("explain the auth function")
    big_code = clf.classify(
        "explain this\n```python\n" + "x = 1\n" * 70 + "```"
    )
    assert big_code.complexity_score > no_code.complexity_score


# ------------------------------------------------------------------ #
# Multi-file signal                                                    #
# ------------------------------------------------------------------ #

def test_multi_file_references_raise_score():
    clf = TaskClassifier(threshold=0.5)
    single = clf.classify("fix the bug in auth.py")
    multi  = clf.classify(
        "fix the bug across auth.py, models.py, middleware.py, views.py, and tests/test_auth.py"
    )
    assert multi.complexity_score > single.complexity_score


# ------------------------------------------------------------------ #
# Threshold configuration                                              #
# ------------------------------------------------------------------ #

def test_custom_threshold_changes_routing():
    prompt = "refactor the login function"

    low_threshold  = TaskClassifier(threshold=0.1)
    high_threshold = TaskClassifier(threshold=0.9)

    assert low_threshold.classify(prompt).route_to  == "paid"
    assert high_threshold.classify(prompt).route_to == "local"


def test_threshold_from_env(monkeypatch):
    monkeypatch.setenv("OCP_ROUTE_THRESHOLD", "0.9")
    clf = TaskClassifier()          # reads env var
    result = clf.classify("explain this function")
    assert result.route_to == "local"   # simple prompt stays local even at low score


# ------------------------------------------------------------------ #
# Signals are populated                                                #
# ------------------------------------------------------------------ #

def test_complex_prompt_has_signals():
    clf = TaskClassifier(threshold=0.5)
    result = clf.classify(
        "review security vulnerabilities across all files in the API"
    )
    assert len(result.signals) > 0


def test_score_clamped_between_0_and_1():
    clf = TaskClassifier(threshold=0.5)
    # Pile on every complex signal to try to exceed 1.0
    prompt = (
        "refactor the entire architecture for security across all files "
        "in production, migrate the database, fix the deadlock and the race condition "
        + "x " * 900
    )
    result = clf.classify(prompt)
    assert 0.0 <= result.complexity_score <= 1.0
