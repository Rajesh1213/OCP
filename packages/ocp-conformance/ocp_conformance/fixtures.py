"""Shared pytest fixtures for the OCP conformance suite."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

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


@pytest_asyncio.fixture
async def ocp_client() -> Any:
    """Start a fresh ocp-server process and yield a connected client.

    The OCPClient.stdio context manager uses anyio cancel scopes internally.
    pytest-asyncio finalises generator fixtures in a *new* asyncio Task via
    event_loop.run_until_complete(), which causes anyio to raise
    "Attempted to exit cancel scope in a different task than it was entered in".

    Fix: run the entire client lifecycle inside a dedicated asyncio Task so
    that all anyio cancel scopes are both entered and exited within that same
    task.  The fixture only exchanges plain asyncio primitives (Event, Task)
    with the pytest-asyncio finalizer, which are safe to use across tasks.
    """
    cmd = _server_cmd()
    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "test.db")
        env = {**os.environ, "OCP_DB_PATH": db}

        ready: asyncio.Event = asyncio.Event()
        stop: asyncio.Event = asyncio.Event()
        client_holder: list[OCPClient] = []
        exc_holder: list[BaseException] = []

        async def _run() -> None:
            try:
                async with OCPClient.stdio([cmd], env=env) as client:
                    client_holder.append(client)
                    ready.set()
                    await stop.wait()
            except BaseException as exc:
                exc_holder.append(exc)
                ready.set()

        task = asyncio.create_task(_run())
        await ready.wait()

        if exc_holder:
            task.cancel()
            raise exc_holder[0]

        yield client_holder[0]

        stop.set()
        await task


@pytest_asyncio.fixture
async def workspace(ocp_client: OCPClient, tmp_path: Path) -> Any:
    """Register a temporary workspace with a few sample files."""
    (tmp_path / "hello.py").write_text("def hello():\n    return 'hello world'\n")
    (tmp_path / "README.md").write_text("# Test workspace\nThis is a test.\n")
    root_uri = f"file://{tmp_path}"
    ws = await ocp_client.workspace_register(root_uri, name="test")
    return ws, ocp_client, tmp_path
