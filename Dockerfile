# TAL memory demo - container image for a persistent web service (Render).
# A long-running uvicorn process, NOT serverless: the migration wizard runs
# background jobs on a thread and the page polls for progress, which needs a
# process that stays alive between requests.
FROM python:3.12-slim

# uv: fast, lockfile-exact installs (copied from the official uv image).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (from the lockfile) so this layer caches when only
# application code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App code + vendored static assets (mermaid.min.js, favicon, logo) + schema.
COPY . .
RUN uv sync --frozen --no-dev

# Render injects $PORT; bind to it (default 8000 for a plain `docker run`).
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uv run --no-dev uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
