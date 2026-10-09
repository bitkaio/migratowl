# Migratowl server: the FastAPI webhook (POST /webhook, GET /jobs, GET /healthz).
# Not the sandbox image (k8s/runtime/) and not the LangGraph-CLI image the release job builds.
#
#   docker build -t migratowl-server .
#   docker run -p 8000:8000 -e ANTHROPIC_API_KEY=... -v migratowl-data:/data migratowl-server

FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS builder

# git: langchain-kubernetes is installed from a git tag (see pyproject.toml); only the build needs it.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.10.4@sha256:4cac394b6b72846f8a85a7a0e577c6d61d4e17fe2ccee65d9451a8b3c9efb4ac /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# The project has no build backend, so uv installs only its dependencies; the source is copied below.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641

RUN useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin migratowl \
    && mkdir /data && chown migratowl:migratowl /data

COPY --from=builder /app/.venv /app/.venv
# Host file modes are not guaranteed (a umask of 077 would leave the source unreadable for UID 1000).
COPY --chmod=u=rwX,go=rX migratowl /app/migratowl

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    MIGRATOWL_JOBS_DB_PATH=/data/migratowl_jobs.db \
    MIGRATOWL_CHECKPOINT_DB_PATH=/data/migratowl_checkpoints.db

USER 1000:1000
WORKDIR /data
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]

# One process: jobs live in SQLite and startup reconciliation assumes a single writer.
CMD ["uvicorn", "migratowl.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
