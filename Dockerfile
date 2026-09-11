# Container image for running the Garmin MCP server

FROM ghcr.io/astral-sh/uv:0.4.20-python3.12-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Copy project metadata and sources early so build backend sees README and package
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Install dependencies and the project (no dev deps)
RUN uv sync --frozen --no-dev

# Create a location to persist Garmin tokens (optional but recommended)

# Default Streamable HTTP port
EXPOSE 8000

# Environment variables to be provided at runtime
# - GARMIN_EMAIL
# - GARMIN_PASSWORD
# - GARMIN_MFA_CODE (optional)
# - GARMIN_MFA_WAIT_SECONDS (optional)
# - GARMIN_MCP_TRANSPORT (defaults to streamable-http)
# - GARMIN_MCP_HOST (defaults to 0.0.0.0)
# - GARMIN_MCP_PORT (defaults to 8000)

# Default command: run the MCP server via uv
ENTRYPOINT ["uv", "run", "garmin-mcp"]


