"""Shared pytest fixtures for the OCP conformance suite."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest_asyncio

from ocp_client import OCPClient


def _server_cmd() -> str:
    """Resolve the ocp-server binary.

    Priority:
    1. OCP_SERVER_CMD env var (explicit override)
    2. ocp-server in the same venv as the running Python (auto-detect)
    3. 'ocp-server' on PATH (fallback)
    """
    if cmd := os.environ.get("OCP_SERVER_CMD"):
        return cmd
    candidate = Path(sys.executable).parent / "ocp-server"
    if candidate.exists():
        return str(candidate)
    return "ocp-server"


# Function-scoped: each test gets its own server process.
# This avoids anyio cancel-scope teardown issues that arise when a session-scoped
# async fixture is finalized in a different asyncio task than it was entered in.
@pytest_asyncio.fixture
async def ocp_client():
    """Start a fresh ocp-server process and yield a connected client."""
    cmd = _server_cmd()
    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "test.db")
        env = {**os.environ, "OCP_DB_PATH": db}
        async with OCPClient.stdio([cmd], env=env) as client:
            yield client


@pytest_asyncio.fixture
async def workspace(ocp_client: OCPClient, tmp_path: Path):
    """Register a temporary workspace with a few sample files."""
    (tmp_path / "hello.py").write_text("def hello():\n    return 'hello world'\n")
    (tmp_path / "README.md").write_text("# Test workspace\nThis is a test.\n")
    root_uri = f"file://{tmp_path}"
    ws = await ocp_client.workspace_register(root_uri, name="test")
    return ws, ocp_client, tmp_path
