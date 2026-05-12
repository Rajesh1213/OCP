FROM python:3.12-slim

WORKDIR /app

# Install uv and curl (curl used by docker-compose healthcheck)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Copy workspace files
COPY pyproject.toml uv.lock ./
COPY packages/ ./packages/

# Install with postgres extras
RUN uv sync --all-packages --extra postgres --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8080/health || exit 1

CMD ["ocp-server-http"]
