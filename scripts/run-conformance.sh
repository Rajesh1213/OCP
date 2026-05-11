#!/usr/bin/env bash
# Run the OCP conformance suite against the reference server.
set -euo pipefail

LEVEL=${OCP_LEVEL:-full}
SERVER_CMD=${OCP_SERVER_CMD:-ocp-server}

uv run ocp-conformance --server-cmd "$SERVER_CMD" --level "$LEVEL" "$@"
