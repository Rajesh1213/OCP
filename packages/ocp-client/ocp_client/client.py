"""OCP client — async, typed wrapper over MCP tool calls."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ocp_client.types import (
    Chunk,
    CheckpointResult,
    HandoffResult,
    IndexResult,
    InvalidateResult,
    OCPError,
    PackResult,
    SearchResult,
    SessionResult,
    SetResult,
    StateEntry,
    SubscriptionResult,
    WorkspaceRegistered,
)


class OCPClient:
    """High-level async client for an OCP server.

    Usage::

        async with OCPClient.stdio(["ocp-server"]) as client:
            ws = await client.workspace_register("file:///my/repo")
            await client.workspace_index(ws.workspace_id)
            results = await client.context_search(ws.workspace_id, "auth middleware")
    """

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Factory                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    @asynccontextmanager
    async def stdio(command: list[str], env: dict[str, str] | None = None) -> AsyncIterator["OCPClient"]:
        params = StdioServerParameters(command=command[0], args=command[1:], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield OCPClient(session)

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    async def _call(self, tool: str, **kwargs: Any) -> Any:
        args = {k: v for k, v in kwargs.items() if v is not None}
        result = await self._session.call_tool(tool, args)
        if not result.content:
            return {}
        raw = result.content[0].text
        data = json.loads(raw)
        if "error" in data:
            raise OCPError(data["error"]["code"], data["error"]["message"])
        return data

    # ------------------------------------------------------------------ #
    # Workspace — §4.1                                                     #
    # ------------------------------------------------------------------ #

    async def workspace_register(
        self, root_uri: str, name: str | None = None, metadata: dict | None = None
    ) -> WorkspaceRegistered:
        data = await self._call("workspace.register", root_uri=root_uri, name=name, metadata=metadata)
        return WorkspaceRegistered(**data)

    async def workspace_index(
        self, workspace_id: str, paths: list[str] | None = None, wait: bool | None = None
    ) -> IndexResult:
        data = await self._call("workspace.index", workspace_id=workspace_id, paths=paths, wait=wait)
        return IndexResult(**data)

    async def workspace_invalidate(self, workspace_id: str, paths: list[str]) -> InvalidateResult:
        data = await self._call("workspace.invalidate", workspace_id=workspace_id, paths=paths)
        return InvalidateResult(**data)

    async def workspace_list_chunks(
        self, workspace_id: str, filters: dict | None = None, cursor: str | None = None
    ) -> tuple[list[Chunk], str | None]:
        data = await self._call("workspace.list_chunks", workspace_id=workspace_id, filters=filters, cursor=cursor)
        return [Chunk(**c) for c in data["chunks"]], data.get("next_cursor")

    # ------------------------------------------------------------------ #
    # Retrieval — §4.2                                                     #
    # ------------------------------------------------------------------ #

    async def context_search(
        self, workspace_id: str, query: str, k: int = 5, filters: dict | None = None
    ) -> SearchResult:
        data = await self._call("context.search", workspace_id=workspace_id, query=query, k=k, filters=filters)
        return SearchResult(chunks=[Chunk(**c) for c in data["chunks"]], scores=data["scores"])

    async def context_get_chunk(self, chunk_id: str) -> Chunk:
        data = await self._call("context.get_chunk", chunk_id=chunk_id)
        return Chunk(**data["chunk"])

    async def context_pack(
        self, workspace_id: str, intent: str, budget_tokens: int, include_state: bool = False
    ) -> PackResult:
        data = await self._call(
            "context.pack", workspace_id=workspace_id, intent=intent,
            budget_tokens=budget_tokens, include_state=include_state,
        )
        return PackResult(**data)

    # ------------------------------------------------------------------ #
    # State — §4.3                                                         #
    # ------------------------------------------------------------------ #

    async def state_set(
        self, key: str, value: Any, scope: str,
        workspace_id: str | None = None, session_id: str | None = None,
        agent_id: str | None = None, ttl_seconds: int | None = None,
        if_version: int | None = None,
    ) -> SetResult:
        data = await self._call(
            "state.set", key=key, value=value, scope=scope,
            workspace_id=workspace_id, session_id=session_id,
            agent_id=agent_id, ttl_seconds=ttl_seconds, if_version=if_version,
        )
        return SetResult(**data)

    async def state_get(
        self, key: str, scope: str | None = None,
        workspace_id: str | None = None, session_id: str | None = None,
        agent_id: str | None = None,
    ) -> StateEntry | None:
        data = await self._call(
            "state.get", key=key, scope=scope,
            workspace_id=workspace_id, session_id=session_id, agent_id=agent_id,
        )
        entry = data.get("entry")
        return StateEntry(**entry) if entry else None

    async def state_list(
        self, prefix: str | None = None, scope: str | None = None,
        workspace_id: str | None = None, session_id: str | None = None,
        agent_id: str | None = None, cursor: str | None = None,
    ) -> tuple[list[StateEntry], str | None]:
        data = await self._call(
            "state.list", prefix=prefix, scope=scope,
            workspace_id=workspace_id, session_id=session_id,
            agent_id=agent_id, cursor=cursor,
        )
        return [StateEntry(**e) for e in data["entries"]], data.get("next_cursor")

    async def state_delete(
        self, key: str, scope: str,
        workspace_id: str | None = None, session_id: str | None = None,
        agent_id: str | None = None, if_version: int | None = None,
    ) -> bool:
        data = await self._call(
            "state.delete", key=key, scope=scope,
            workspace_id=workspace_id, session_id=session_id,
            agent_id=agent_id, if_version=if_version,
        )
        return data["deleted"]

    # ------------------------------------------------------------------ #
    # Coordination — §4.4                                                  #
    # ------------------------------------------------------------------ #

    async def session_open(
        self, workspace_id: str, session_id: str | None = None,
        ttl_seconds: int | None = None, metadata: dict | None = None,
    ) -> SessionResult:
        data = await self._call(
            "session.open", workspace_id=workspace_id, session_id=session_id,
            ttl_seconds=ttl_seconds, metadata=metadata,
        )
        return SessionResult(**data)

    async def session_close(self, session_id: str) -> bool:
        data = await self._call("session.close", session_id=session_id)
        return data["closed"]

    async def session_handoff(
        self, session_id: str, from_agent: str, to_agent: str, message: Any
    ) -> HandoffResult:
        data = await self._call(
            "session.handoff", session_id=session_id,
            from_agent=from_agent, to_agent=to_agent, message=message,
        )
        return HandoffResult(**data)

    async def session_checkpoint(
        self, session_id: str, label: str, include_state: bool = False
    ) -> CheckpointResult:
        data = await self._call(
            "session.checkpoint", session_id=session_id,
            label=label, include_state=include_state,
        )
        return CheckpointResult(**data)

    async def session_restore(self, checkpoint_id: str) -> SessionResult:
        data = await self._call("session.restore", checkpoint_id=checkpoint_id)
        return SessionResult(**data)

    # ------------------------------------------------------------------ #
    # Events — §4.5                                                        #
    # ------------------------------------------------------------------ #

    async def events_subscribe(
        self, workspace_id: str, types: list[str] | None = None,
        session_id: str | None = None, since: str | None = None,
    ) -> SubscriptionResult:
        data = await self._call(
            "events.subscribe", workspace_id=workspace_id,
            types=types, session_id=session_id, since=since,
        )
        return SubscriptionResult(**data)

    async def events_unsubscribe(self, subscription_id: str) -> bool:
        data = await self._call("events.unsubscribe", subscription_id=subscription_id)
        return data["unsubscribed"]
