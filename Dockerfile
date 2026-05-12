FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy workspace files
COPY pyproject.toml uv.lock ./
COPY packages/ ./packages/

# Install with postgres extras
RUN uv sync --all-packages --extra postgres --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

CMD ["ocp-server-http"]
