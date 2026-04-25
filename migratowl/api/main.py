# Copyright bitkaio LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""FastAPI application — webhook entrypoint for Migratowl scans."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv

load_dotenv()  # inject .env into os.environ so third-party SDKs (anthropic, etc.) can read it

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from langchain_kubernetes import KubernetesSandboxManager  # noqa: E402

from migratowl.api.helpers import build_user_message, extract_report  # noqa: E402
from migratowl.api.jobs import JobStore  # noqa: E402
from migratowl.config import Settings, get_settings  # noqa: E402
from migratowl.git.notify import notify_pr_done, notify_pr_failed, notify_pr_start  # noqa: E402
from migratowl.http import close_http_client  # noqa: E402
from migratowl.models.schemas import (  # noqa: E402
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
    manager: KubernetesSandboxManager | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    When ``manager`` is supplied (e.g. in tests), the lifespan handler skips
    K8s init and uses it directly.
    """
    if settings is None:
        settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        global _scan_semaphore
        _scan_semaphore = asyncio.Semaphore(1)

        if manager is not None:
            # Pre-initialized (tests or external setup)
            app.state.manager = manager
        else:
            from migratowl.agent.sandbox import create_sandbox_manager

            app.state.manager = create_sandbox_manager(settings)

        app.state.job_store = JobStore()
        app.state.settings = settings

        yield

        # Shutdown
        await close_http_client()
        if hasattr(app.state, "manager"):
            await app.state.manager.ashutdown()

    app = FastAPI(title="Migratowl", lifespan=lifespan)

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

    @app.get("/jobs/{job_id}", response_model=None)
    async def get_job(job_id: str) -> JobStatus | JSONResponse:
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
        await notify_pr_start(job.payload, app.state.settings)
        try:
            from migratowl.agent.factory import create_migratowl_agent

            graph = create_migratowl_agent(
                app.state.manager,
                settings=app.state.settings,
                mode=job.payload.mode,
                include_prerelease=job.payload.include_prerelease,
            )
            user_msg = build_user_message(job.payload)
            result = await graph.ainvoke(
                {"messages": [("user", user_msg)]},
                config={"configurable": {"thread_id": job_id}},
            )
            report = extract_report(result, job.payload)
            report.model_name = app.state.settings.model_name
            store.set_result(job_id, report)

            # Optional callback
            if job.payload.callback_url:
                await _post_callback(job.payload.callback_url, report)

            await notify_pr_done(job.payload, report, app.state.settings)

        except Exception:
            logger.exception("Scan failed for job %s", job_id)
            store.set_error(job_id, "Internal scan error")
            await notify_pr_failed(job.payload, app.state.settings)


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