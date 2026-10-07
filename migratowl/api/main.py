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
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv

load_dotenv()  # inject .env into os.environ so third-party SDKs (anthropic, etc.) can read it

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from langchain_core.runnables import RunnableConfig  # noqa: E402
from langchain_kubernetes import KubernetesSandboxManager  # noqa: E402

from migratowl.api.helpers import (  # noqa: E402
    ReportExtractionError,
    TokenUsage,
    assemble_report,
    extract_verdicts,
    sum_usage,
)
from migratowl.api.jobs import JobStore, create_job_store  # noqa: E402
from migratowl.config import Settings, get_settings  # noqa: E402
from migratowl.git.notify import notify_pr_done, notify_pr_failed, notify_pr_start  # noqa: E402
from migratowl.http import close_http_client  # noqa: E402
from migratowl.models.schemas import (  # noqa: E402
    JobState,
    JobStatus,
    ScanWebhookPayload,
    WebhookAcceptedResponse,
)
from migratowl.pipeline import PipelineError  # noqa: E402

logger = logging.getLogger(__name__)


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
        from migratowl.patches import apply_patches

        # Ensure third-party monkey-patches are applied on the webhook path too
        # (previously only applied when agent/graph.py was imported).
        apply_patches()

        # Resource throttle: one scan at a time by default. Each job runs in its
        # own sandbox pod (keyed by thread_id == job_id), so this bounds sandbox
        # and LLM load rather than preventing workspace-path collisions.
        app.state.scan_semaphore = asyncio.Semaphore(settings.max_concurrent_scans)

        # Shared LLM rate limiter across all concurrent scans, so raising
        # max_concurrent_scans does not multiply total API RPS.
        from langchain_core.rate_limiters import InMemoryRateLimiter

        app.state.rate_limiter = InMemoryRateLimiter(
            requests_per_second=settings.model_rate_limit_rps,
            check_every_n_seconds=0.1,
            max_bucket_size=1,
        )

        if manager is not None:
            # Pre-initialized (tests or external setup)
            app.state.manager = manager
        else:
            from migratowl.agent.sandbox import create_sandbox_manager

            app.state.manager = create_sandbox_manager(settings)

        app.state.job_store = create_job_store(settings)
        app.state.settings = settings
        # Track in-flight scan tasks: keeps a strong reference (else the loop may
        # GC them mid-flight) and lets shutdown mark them INTERRUPTED.
        app.state.tasks = set()

        # Durable agent-state checkpointer, held open for the app lifetime via an
        # AsyncExitStack. Keyed by thread_id == job_id, it lets an interrupted
        # scan resume from its last super-step.
        from contextlib import AsyncExitStack

        from migratowl.api.checkpoint import create_checkpointer

        async with AsyncExitStack() as stack:
            app.state.checkpointer = await stack.enter_async_context(
                create_checkpointer(settings)
            )

            # Recover jobs orphaned by a previous crash BEFORE the TTL sweep, so
            # the sweep can skip sandboxes still referenced by non-terminal jobs.
            from migratowl.api.reconcile import reconcile_orphans

            reconcile_orphans(app.state.job_store, settings)

            # Reap leaked sandboxes past their TTL, skipping pods still owned by
            # non-terminal (resumable) jobs. Guarded — a cluster hiccup at startup
            # must not abort the app.
            await _sweep_orphan_sandboxes(app)

            yield

            # Shutdown: proactively mark in-flight scans INTERRUPTED so a rolling
            # deploy leaves them resumable rather than relying on next-boot
            # reconcile (stack closes the checkpointer connection on exit).
            for job in app.state.job_store.list_by_state(JobState.RUNNING):
                app.state.job_store.update_state(job.job_id, JobState.INTERRUPTED)
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
        task = asyncio.create_task(_run_scan(app, job.job_id))
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)
        return WebhookAcceptedResponse(
            job_id=job.job_id,
            status_url=f"/jobs/{job.job_id}",
        )

    @app.get("/jobs", response_model=None)
    async def list_jobs(state: str | None = None) -> dict | JSONResponse:
        """List jobs, optionally filtered by state — lets operators see interrupted work."""
        store: JobStore = app.state.job_store
        if state is None:
            return JSONResponse(
                status_code=400,
                content={"detail": "Query param 'state' is required (e.g. ?state=interrupted)"},
            )
        try:
            job_state = JobState(state)
        except ValueError:
            return JSONResponse(
                status_code=422, content={"detail": f"Invalid state '{state}'"}
            )
        jobs = store.list_by_state(job_state)
        return {"jobs": [j.model_dump(mode="json") for j in jobs]}

    @app.get("/jobs/{job_id}", response_model=None)
    async def get_job(job_id: str) -> JobStatus | JSONResponse:
        store: JobStore = app.state.job_store
        job = store.get(job_id)
        if job is None:
            return JSONResponse(status_code=404, content={"detail": "Job not found"})
        return job

    @app.post("/jobs/{job_id}/resume", status_code=202, response_model=None)
    async def resume_job(job_id: str) -> WebhookAcceptedResponse | JSONResponse:
        store: JobStore = app.state.job_store
        settings_: Settings = app.state.settings
        job = store.get(job_id)
        if job is None:
            return JSONResponse(status_code=404, content={"detail": "Job not found"})

        # Atomic INTERRUPTED -> RUNNING claim; only the winner proceeds. Guards
        # against concurrent/double resume of the same job (checkpoint corruption).
        if not store.claim_for_resume(job_id):
            return JSONResponse(
                status_code=409,
                content={"detail": f"Job not resumable in state '{job.state.value}'"},
            )

        new_count = store.increment_retry(job_id)
        if new_count > settings_.max_scan_retries:
            store.set_error(job_id, "Exceeded max scan retries")
            return JSONResponse(
                status_code=409, content={"detail": "Exceeded max scan retries"}
            )

        task = asyncio.create_task(_run_scan(app, job_id, resume=True))
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)
        return WebhookAcceptedResponse(job_id=job_id, status_url=f"/jobs/{job_id}")

    return app


async def _sweep_orphan_sandboxes(app: FastAPI) -> None:
    """Reap leaked sandboxes past their TTL at startup.

    Guarded so a cluster error does not abort the app. TTL-based cleanup will not
    touch an actively-heartbeating pod, and the absolute TTL defaults to None to
    avoid reaping long-running scans; runs AFTER reconcile so resumable jobs'
    pods are known.
    """
    manager = getattr(app.state, "manager", None)
    if manager is None:
        return
    try:
        result = await manager.acleanup()
        deleted = getattr(result, "deleted", None)
        if deleted:
            logger.info("Startup sweep deleted leaked sandboxes: %s", deleted)
    except Exception:
        logger.warning("Startup sandbox sweep failed (non-fatal)", exc_info=True)


async def _run_scan(app: FastAPI, job_id: str, *, resume: bool = False) -> None:
    """Background task: run agent scan for a job.

    On ``resume`` the job is already RUNNING (claimed via ``claim_for_resume``);
    we reconnect to the surviving sandbox or clear the checkpoint to restart, then
    re-invoke the graph on the same thread_id.
    """
    store: JobStore = app.state.job_store
    job = store.get(job_id)
    if job is None:
        return

    semaphore: asyncio.Semaphore = app.state.scan_semaphore
    async with semaphore:
        if not resume:
            store.update_state(job_id, JobState.RUNNING)
        await notify_pr_start(job.payload, app.state.settings)

        # Persist sandbox_id the moment the sandbox is acquired (lazily, on the
        # first tool call, which runs on a worker thread). get_running_loop() is
        # captured here on the event-loop thread; call_soon_threadsafe schedules
        # the sync store write back onto the loop, so this is safe to invoke from
        # the tool's worker thread (asyncio.create_task would raise there).
        loop = asyncio.get_running_loop()

        def _persist_sandbox_id(sandbox_id: str) -> None:
            loop.call_soon_threadsafe(store.set_sandbox_id, job_id, sandbox_id)

        try:
            if resume:
                from migratowl.api.resume import reconnect_or_restart

                await reconnect_or_restart(
                    app.state.manager, job, store, app.state.checkpointer
                )

            from migratowl.agent.factory import build_tools, create_migratowl_agent
            from migratowl.pipeline import build_analysis_brief, prepare_scan, presolve

            settings = app.state.settings
            config: RunnableConfig = {"configurable": {"thread_id": job_id}}
            started = time.monotonic()
            tools = build_tools(
                app.state.manager,
                settings=settings,
                mode=job.payload.mode,
                include_prerelease=job.payload.include_prerelease,
                on_sandbox_acquired=_persist_sandbox_id,
            )
            prepared = await prepare_scan(tools, job.payload, config, tail_chars=settings.analysis_tail_chars)
            resolved, pending = presolve(prepared)

            verdicts: list = []
            tokens = TokenUsage()
            if pending:
                from langchain_core.callbacks import UsageMetadataCallbackHandler

                usage = UsageMetadataCallbackHandler()
                graph = create_migratowl_agent(
                    app.state.manager,
                    tools=tools,
                    include_prepare_scan=False,
                    settings=settings,
                    mode=job.payload.mode,
                    include_prerelease=job.payload.include_prerelease,
                    rate_limiter=getattr(app.state, "rate_limiter", None),
                    checkpointer=getattr(app.state, "checkpointer", None),
                    usage_callback=usage,
                )
                result = await graph.ainvoke(
                    {"messages": [("user", build_analysis_brief(job.payload, prepared, pending))]},
                    config=config,
                )
                verdicts = extract_verdicts(result)
                tokens = sum_usage(usage.usage_metadata.values())

            report = assemble_report(
                job.payload, prepared, resolved + verdicts, duration=time.monotonic() - started, tokens=tokens
            )
            report.model_name = settings.model_name
            store.set_result(job_id, report)

            # Terminal side effects fire at most once across the original run and
            # any resumes — guarded so a resumed job never posts a duplicate PR
            # comment or re-fires the callback.
            if not job.side_effects_done:
                if job.payload.callback_url:
                    await _post_callback(job.payload.callback_url, report)
                await notify_pr_done(job.payload, report, app.state.settings)
                store.mark_side_effects_done(job_id)

        except (PipelineError, ReportExtractionError) as exc:
            logger.error("Scan for job %s produced no report: %s", job_id, exc)
            store.set_error(job_id, str(exc))
            if not job.side_effects_done:
                await notify_pr_failed(job.payload, app.state.settings)
        except Exception:
            logger.exception("Scan failed for job %s", job_id)
            store.set_error(job_id, "Internal scan error")
            if not job.side_effects_done:
                await notify_pr_failed(job.payload, app.state.settings)

        # Terminal outcome: free the sandbox now instead of leaving the pod running
        # until shutdown or the TTL sweep. Not reached on cancellation (shutdown),
        # so interrupted jobs keep their sandbox for resume.
        await _release_sandbox(app.state.manager, job_id)


async def _release_sandbox(manager: Any, job_id: str) -> None:
    """Delete the sandbox bound to ``job_id`` (thread_id), if one was acquired.

    Uses the same manager internals as ``resume.py``.
    """
    sandbox = manager._sandbox_by_thread.pop(job_id, None)
    if sandbox is None:
        return
    try:
        await manager._provider.adelete(sandbox_id=sandbox.id)
    except Exception:
        logger.warning("Failed to delete sandbox %s for job %s", sandbox.id, job_id, exc_info=True)


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