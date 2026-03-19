"""FastAPI application — webhook entrypoint for MigratOwl scans."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv

load_dotenv()  # inject .env into os.environ so third-party SDKs (anthropic, etc.) can read it

from deepagents.backends.protocol import BackendProtocol
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from langchain_kubernetes import KubernetesProvider

from migratowl.api.helpers import build_user_message, extract_report
from migratowl.api.jobs import JobStore
from migratowl.config import Settings, get_settings
from migratowl.http import close_http_client
from migratowl.models.schemas import (
    JobState,
    JobStatus,
    ScanWebhookPayload,
    WebhookAcceptedResponse,
)

logger = logging.getLogger(__name__)

# v1 limitation: one scan at a time to avoid workspace path collisions.
_scan_semaphore: asyncio.Semaphore | None = None


def create_app(
    *,
    settings: Settings | None = None,
    sandbox: BackendProtocol | None = None,
    provider: KubernetesProvider | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    When ``sandbox`` and ``provider`` are supplied (e.g. in tests), the lifespan
    handler skips K8s init and uses them directly.
    """
    if settings is None:
        settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        global _scan_semaphore
        _scan_semaphore = asyncio.Semaphore(1)

        if sandbox is not None and provider is not None:
            # Pre-initialized (tests or external setup)
            app.state.sandbox = sandbox
            app.state.provider = provider
        else:
            from migratowl.agent.sandbox import create_sandbox

            app.state.provider, app.state.sandbox = await create_sandbox(settings)

        app.state.job_store = JobStore()
        app.state.settings = settings

        yield

        # Shutdown
        await close_http_client()
        if hasattr(app.state, "provider") and hasattr(app.state, "sandbox"):
            from migratowl.agent.sandbox import destroy_sandbox

            await destroy_sandbox(app.state.provider, app.state.sandbox)

    app = FastAPI(title="MigratOwl", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook", status_code=202)
    async def webhook(payload: ScanWebhookPayload) -> WebhookAcceptedResponse:
        store: JobStore = app.state.job_store
        job = store.create(payload)
        asyncio.create_task(_run_scan(app, job.job_id))
        return WebhookAcceptedResponse(
            job_id=job.job_id,
            status_url=f"/jobs/{job.job_id}",
        )

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str) -> JobStatus:
        store: JobStore = app.state.job_store
        job = store.get(job_id)
        if job is None:
            return JSONResponse(status_code=404, content={"detail": "Job not found"})
        return job

    return app


async def _run_scan(app: FastAPI, job_id: str) -> None:
    """Background task: run agent scan for a job."""
    global _scan_semaphore
    store: JobStore = app.state.job_store
    job = store.get(job_id)
    if job is None:
        return

    assert _scan_semaphore is not None
    async with _scan_semaphore:
        store.update_state(job_id, JobState.RUNNING)
        try:
            from migratowl.agent.factory import create_migratowl_agent

            graph = create_migratowl_agent(
                app.state.sandbox, settings=app.state.settings
            )
            user_msg = build_user_message(job.payload)
            result = await graph.ainvoke(
                {"messages": [("user", user_msg)]},
                config={"configurable": {"thread_id": job_id}},
            )
            report = extract_report(result, job.payload)
            store.set_result(job_id, report)

            # Optional callback
            if job.payload.callback_url:
                await _post_callback(job.payload.callback_url, report)

        except Exception:
            logger.exception("Scan failed for job %s", job_id)
            store.set_error(job_id, "Internal scan error")


async def _post_callback(callback_url: str, report: Any) -> None:
    """POST result to the caller's callback URL."""
    try:
        from migratowl.http import get_http_client

        client = get_http_client()
        resp = await client.post(
            callback_url,
            json=report.model_dump(mode="json"),
            timeout=30.0,
        )
        logger.info("Callback POST to %s returned %s", callback_url, resp.status_code)
    except Exception:
        logger.warning("Failed to POST callback to %s", callback_url, exc_info=True)


# Module-level app for ``uvicorn migratowl.api.main:app``
app = create_app()
