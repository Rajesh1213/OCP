"""Shared pytest fixtures for the OCP conformance suite."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from ocp_client import OCPClient


@pytest_asyncio.fixture(scope="session")
async def ocp_client():
    """Start a fresh ocp-server process and yield a connected client."""
    server_cmd = os.environ.get("OCP_SERVER_CMD", "ocp-server")
    with tempfile.TemporaryDirectory() as tmpdir:
        db = os.path.join(tmpdir, "test.db")
        env = {**os.environ, "OCP_DB_PATH": db}
        async with OCPClient.stdio([server_cmd], env=env) as client:
            yield client


@pytest_asyncio.fixture
async def workspace(ocp_client: OCPClient, tmp_path: Path):
    """Register a temporary workspace with a few sample files."""
    (tmp_path / "hello.py").write_text("def hello():\n    return 'hello world'\n")
    (tmp_path / "README.md").write_text("# Test workspace\nThis is a test.\n")
    root_uri = f"file://{tmp_path}"
    ws = await ocp_client.workspace_register(root_uri, name="test")
    return ws, ocp_client, tmp_path
