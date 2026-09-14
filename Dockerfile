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

# Default Streamable HTTP port (MCP server) and the personal dashboard port
EXPOSE 8000
EXPOSE 8080

# Environment variables to be provided at runtime
# - GARMIN_EMAIL
# - GARMIN_PASSWORD
# - GARMIN_MFA_CODE (optional)
# - GARMIN_MFA_WAIT_SECONDS (optional)
# - GARMIN_MCP_TRANSPORT (defaults to streamable-http)
# - GARMIN_MCP_HOST (defaults to 0.0.0.0)
# - GARMIN_MCP_PORT (defaults to 8000)
#
# Personal training dashboard (see README "Persönliches Trainings-Dashboard"):
# - DASHBOARD_USERNAME / DASHBOARD_PASSWORD (required to start the dashboard)
# - ANTHROPIC_API_KEY (optional, enables the AI training advisor chat)
# - DASHBOARD_HOST / DASHBOARD_PORT (default 0.0.0.0 / 8080)
#
# This image runs one process at a time. Deploy the MCP server and the
# dashboard as two separate containers/Deployments from the same image,
# overriding the command: `uv run garmin-mcp` vs. `uv run garmin-dashboard`.

# Default command: run the MCP server via uv
ENTRYPOINT ["uv", "run", "garmin-mcp"]


