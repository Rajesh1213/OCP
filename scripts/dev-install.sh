#!/usr/bin/env bash
# Install the full OCP workspace in editable mode.
set -euo pipefail

if ! command -v uv &>/dev/null; then
  echo "uv not found — install from https://docs.astral.sh/uv/"
  exit 1
fi

uv sync --all-packages
echo "Done. Activate with: source .venv/bin/activate"
