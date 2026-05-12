"""OCP authentication and workspace authorisation layer.

Design
------
Auth is opt-in. If OCP_API_KEYS is unset the server runs in open/dev mode and
every request is treated as fully-authorised. Set OCP_API_KEYS to enable.

Environment variables
---------------------
OCP_API_KEYS
    Comma-separated list of valid API keys that have access to ALL workspaces.
    Example: OCP_API_KEYS=key-prod-abc123,key-ci-def456

OCP_API_KEY_WORKSPACES
    Semicolon-separated list of <key>:<ws_id1>,<ws_id2> pairs that restrict a
    key to specific workspaces. Workspaces listed here override any entry in
    OCP_API_KEYS for that key (OCP_API_KEY_WORKSPACES takes precedence).
    Example: OCP_API_KEY_WORKSPACES=key-tenantA:ws_abc,ws_def;key-tenantB:ws_xyz

Usage in stdio mode
-------------------
Set OCP_API_KEY=<key> in the environment before spawning ocp-server. The
server reads OCP_API_KEY from the process environment during initialization.

Usage in HTTP mode
------------------
Pass the key as a Bearer token:
    Authorization: Bearer <key>

The HTTP middleware extracts the token and stores it in the auth context var
for the duration of that request.
"""
from __future__ import annotations

import contextvars
import os
from dataclasses import dataclass, field
from typing import Mapping


# ------------------------------------------------------------------ #
# Context variable — one AuthContext per asyncio task (= per request) #
# ------------------------------------------------------------------ #

_auth_ctx: contextvars.ContextVar["AuthContext"] = contextvars.ContextVar("ocp_auth_ctx")


@dataclass(frozen=True)
class AuthContext:
    """Immutable auth state for one request / connection."""

    api_key: str
    # None means "all workspaces allowed"
    allowed_workspaces: frozenset[str] | None = None
    dev_mode: bool = False          # True when auth is disabled globally

    def can_access_workspace(self, workspace_id: str) -> bool:
        if self.dev_mode:
            return True
        if self.allowed_workspaces is None:
            return True
        return workspace_id in self.allowed_workspaces

    def assert_workspace(self, workspace_id: str) -> None:
        """Raise PermissionDeniedError if this context cannot access workspace_id."""
        if not self.can_access_workspace(workspace_id):
            raise PermissionDeniedError(
                f"API key does not have access to workspace {workspace_id}"
            )


# Sentinel used when the server is in dev mode (no auth configured)
_DEV_CONTEXT = AuthContext(api_key="__dev__", dev_mode=True)


def get_auth_context() -> AuthContext:
    """Return the AuthContext for the current request.

    Falls back to the dev context if none has been set (should only happen in
    test code that bypasses the server wiring).
    """
    return _auth_ctx.get(_DEV_CONTEXT)


def set_auth_context(ctx: AuthContext) -> contextvars.Token:
    return _auth_ctx.set(ctx)


def reset_auth_context(token: contextvars.Token) -> None:
    _auth_ctx.reset(token)


# ------------------------------------------------------------------ #
# Auth config                                                          #
# ------------------------------------------------------------------ #

@dataclass
class AuthConfig:
    """Parsed auth configuration derived from environment variables."""

    # key → frozenset of workspace IDs, or None for unrestricted
    key_map: dict[str, frozenset[str] | None] = field(default_factory=dict)
    enabled: bool = False

    def validate_key(self, key: str) -> AuthContext | None:
        """Return an AuthContext for a valid key, or None if the key is invalid."""
        if not self.enabled:
            return _DEV_CONTEXT
        if key not in self.key_map:
            return None
        return AuthContext(api_key=key, allowed_workspaces=self.key_map[key])


def load_auth_config() -> AuthConfig:
    """Build AuthConfig from environment variables."""
    cfg = AuthConfig()

    raw_keys = os.environ.get("OCP_API_KEYS", "").strip()
    for k in (k.strip() for k in raw_keys.split(",") if k.strip()):
        cfg.key_map[k] = None   # unrestricted
        cfg.enabled = True

    raw_scoped = os.environ.get("OCP_API_KEY_WORKSPACES", "").strip()
    for entry in (e.strip() for e in raw_scoped.split(";") if e.strip()):
        if ":" not in entry:
            continue
        key, workspaces_str = entry.split(":", 1)
        key = key.strip()
        ws = frozenset(w.strip() for w in workspaces_str.split(",") if w.strip())
        cfg.key_map[key] = ws
        cfg.enabled = True

    return cfg


# ------------------------------------------------------------------ #
# Errors                                                               #
# ------------------------------------------------------------------ #

class PermissionDeniedError(Exception):
    code = "PERMISSION_DENIED"


class UnauthorisedError(Exception):
    """Raised at the transport level when no valid key is presented."""
    code = "UNAUTHORISED"
