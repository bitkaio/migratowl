# SPDX-License-Identifier: Apache-2.0

"""Tests for FastAPI webhook app."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import migratowl.api.main as main_mod
from httpx import AsyncClient, ASGITransport

from migratowl.api.jobs import InMemoryJobStore
from migratowl.config import Settings


def _ok_result() -> dict:
    """Minimal successful agent result: verdicts for the fake pipeline's one pending package."""
    from migratowl.models.schemas import AnalysisReport, PackageVerdicts

    verdict = AnalysisReport(dependency_name="flask", is_breaking=True, error_summary="ImportError",
                             changelog_citation="", suggested_human_fix="", confidence=0.9)
    return {"messages": [], "structured_response": PackageVerdicts(reports=[verdict])}


def _prepared_scan(*, pending: bool = True):
    from migratowl.models.schemas import Ecosystem, OutdatedDependency, ScanResult
    from migratowl.pipeline import EcosystemValidation, PreparedScan

    dep = OutdatedDependency(name="flask", current_version="2.0", latest_version="3.0" if pending else "2.1",
                             ecosystem=Ecosystem.PYTHON, manifest_path="pyproject.toml")
    validation = EcosystemValidation(
        ecosystem="python",
        passed=not pending,
        failed_step="test" if pending else None,
        output_tail="ImportError: flask" if pending else "",
    )
    return PreparedScan(
        scan_result=ScanResult(
            all_deps=[], outdated=[dep], manifests_found=["pyproject.toml"], scan_duration_seconds=0.0
        ),
        candidates=[dep],
        skipped=[],
        validations=[validation],
    )


@pytest.fixture(autouse=True)
def fake_pipeline():
    """Every _run_scan test gets a pipeline that needs the LLM for one package (flask)."""
    with patch("migratowl.agent.factory.build_tools", return_value=MagicMock()) as mock_build, \
         patch("migratowl.pipeline.prepare_scan", AsyncMock(return_value=_prepared_scan())) as mock_prepare:
        yield SimpleNamespace(build_tools=mock_build, prepare_scan=mock_prepare)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def mock_manager() -> MagicMock:
    from langchain_kubernetes import KubernetesSandboxManager

    mgr = MagicMock(spec=KubernetesSandboxManager)
    # Async methods the lifespan awaits.
    mgr.acleanup = AsyncMock(return_value=MagicMock(deleted=[]))
    mgr.ashutdown = AsyncMock()
    # Instance attributes (not on the class spec) that _run_scan's sandbox release reads.
    mgr._sandbox_by_thread = {}
    mgr._provider = MagicMock(adelete=AsyncMock())
    return mgr


@pytest.fixture
def app(settings: Settings, mock_manager: MagicMock):
    """Create app with pre-initialized manager (skip lifespan K8s init)."""
    application = main_mod.create_app(settings=settings, manager=mock_manager)
    # Manually set state that lifespan would set (ASGITransport doesn't trigger lifespan)
    application.state.manager = mock_manager
    application.state.job_store = InMemoryJobStore()
    application.state.settings = settings
    application.state.scan_semaphore = asyncio.Semaphore(1)
    application.state.tasks = set()
    return application


@pytest.fixture
async def client(app) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthz:
    @pytest.mark.asyncio
    async def test_returns_ok(self, client: AsyncClient) -> None:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestWebhook:
    @pytest.mark.asyncio
    async def test_accepts_valid_payload(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/webhook",
            json={"repo_url": "https://github.com/x/y"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert "status_url" in data
        assert data["status_url"].startswith("/jobs/")

    @pytest.mark.asyncio
    async def test_rejects_missing_repo_url(self, client: AsyncClient) -> None:
        resp = await client.post("/webhook", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_returns_unique_job_ids(self, client: AsyncClient) -> None:
        resp1 = await client.post(
            "/webhook", json={"repo_url": "https://github.com/x/y"}
        )
        resp2 = await client.post(
            "/webhook", json={"repo_url": "https://github.com/x/y"}
        )
        assert resp1.json()["job_id"] != resp2.json()["job_id"]


class TestGetJob:
    @pytest.mark.asyncio
    async def test_returns_job_after_webhook(self, client: AsyncClient) -> None:
        post_resp = await client.post(
            "/webhook", json={"repo_url": "https://github.com/x/y"}
        )
        job_id = post_resp.json()["job_id"]

        get_resp = await client.get(f"/jobs/{job_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["job_id"] == job_id
        assert data["state"] in ("pending", "running", "completed", "failed", "interrupted")

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_job(self, client: AsyncClient) -> None:
        resp = await client.get("/jobs/nonexistent-id")
        assert resp.status_code == 404


class TestWebhookNotifyIntegration:
    @pytest.mark.asyncio
    async def test_notify_pr_start_called_when_pr_and_sha_provided(
        self, app, client: AsyncClient
    ) -> None:
        with patch("migratowl.api.main.notify_pr_start") as mock_start, \
             patch("migratowl.api.main.notify_pr_done"), \
             patch("migratowl.agent.factory.create_migratowl_agent") as mock_agent:
            mock_agent.return_value.ainvoke = AsyncMock(return_value=_ok_result())
            mock_start.return_value = None

            await client.post(
                "/webhook",
                json={
                    "repo_url": "https://github.com/x/y",
                    "pr_number": 5,
                    "commit_sha": "abc123",
                },
            )
            await asyncio.sleep(0.05)

        mock_start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_pr_done_called_on_success(
        self, app, client: AsyncClient
    ) -> None:
        with patch("migratowl.api.main.notify_pr_start"), \
             patch("migratowl.api.main.notify_pr_done") as mock_done, \
             patch("migratowl.agent.factory.create_migratowl_agent") as mock_agent:
            mock_agent.return_value.ainvoke = AsyncMock(return_value=_ok_result())

            await client.post(
                "/webhook",
                json={"repo_url": "https://github.com/x/y", "pr_number": 5},
            )
            await asyncio.sleep(0.05)

        mock_done.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_notify_pr_failed_called_on_scan_error(
        self, app, client: AsyncClient
    ) -> None:
        with patch("migratowl.api.main.notify_pr_start"), \
             patch("migratowl.api.main.notify_pr_done"), \
             patch("migratowl.api.main.notify_pr_failed") as mock_failed, \
             patch("migratowl.agent.factory.create_migratowl_agent") as mock_agent:
            mock_agent.return_value.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))

            await client.post(
                "/webhook",
                json={
                    "repo_url": "https://github.com/x/y",
                    "pr_number": 5,
                    "commit_sha": "abc123",
                },
            )
            await asyncio.sleep(0.05)

        mock_failed.assert_awaited_once()


class TestLifespanConfiguration:
    @pytest.mark.asyncio
    async def test_semaphore_uses_configured_concurrency(self, mock_manager: MagicMock) -> None:
        """The lifespan builds the scan semaphore from settings.max_concurrent_scans."""
        settings = Settings(_env_file=None, max_concurrent_scans=3, persistence_backend="memory")
        application = main_mod.create_app(settings=settings, manager=mock_manager)
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test"):
            # ASGITransport alone doesn't run lifespan; trigger it explicitly.
            async with application.router.lifespan_context(application):
                assert application.state.scan_semaphore._value == 3

    @pytest.mark.asyncio
    async def test_lifespan_applies_patches(self, mock_manager: MagicMock) -> None:
        """The webhook path must apply monkey-patches (previously only graph.py did)."""
        settings = Settings(_env_file=None, persistence_backend="memory")
        application = main_mod.create_app(settings=settings, manager=mock_manager)
        with patch("migratowl.patches.apply_patches") as mock_patches:
            async with application.router.lifespan_context(application):
                mock_patches.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_builds_shared_rate_limiter(self, mock_manager: MagicMock) -> None:
        """One shared rate limiter is created so concurrent scans don't multiply API RPS."""
        settings = Settings(_env_file=None, persistence_backend="memory")
        application = main_mod.create_app(settings=settings, manager=mock_manager)
        async with application.router.lifespan_context(application):
            assert application.state.rate_limiter is not None

    @pytest.mark.asyncio
    async def test_lifespan_builds_checkpointer(self, mock_manager: MagicMock) -> None:
        """The lifespan opens a checkpointer and exposes it on app.state."""
        settings = Settings(_env_file=None, persistence_backend="memory")
        application = main_mod.create_app(settings=settings, manager=mock_manager)
        async with application.router.lifespan_context(application):
            assert application.state.checkpointer is not None

    @pytest.mark.asyncio
    async def test_lifespan_reconciles_before_sweep(self, mock_manager: MagicMock) -> None:
        """Orphan reconciliation runs, and the TTL sweep runs after it."""
        settings = Settings(_env_file=None, persistence_backend="memory")
        application = main_mod.create_app(settings=settings, manager=mock_manager)
        order: list[str] = []
        with (
            patch(
                "migratowl.api.reconcile.reconcile_orphans",
                side_effect=lambda *a, **k: order.append("reconcile"),
            ),
            patch(
                "migratowl.api.main._sweep_orphan_sandboxes",
                new=AsyncMock(side_effect=lambda *a, **k: order.append("sweep")),
            ),
        ):
            async with application.router.lifespan_context(application):
                pass
        assert order == ["reconcile", "sweep"]


class TestRunScanSetsModelName:
    @pytest.mark.asyncio
    async def test_model_name_set_on_report(self, app) -> None:
        from migratowl.models.schemas import ScanWebhookPayload

        with patch("migratowl.agent.factory.create_migratowl_agent") as mock_factory, \
             patch("migratowl.api.main.notify_pr_done", new_callable=AsyncMock), \
             patch("migratowl.api.main.notify_pr_start", new_callable=AsyncMock):
            mock_graph = AsyncMock()
            mock_graph.ainvoke.return_value = _ok_result()
            mock_factory.return_value = mock_graph

            payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
            job = app.state.job_store.create(payload)

            await main_mod._run_scan(app, job.job_id)

            result_job = app.state.job_store.get(job.job_id)
            assert result_job.result is not None
            assert result_job.result.model_name == app.state.settings.model_name

    @pytest.mark.asyncio
    async def test_run_scan_forwards_checkpointer_and_rate_limiter(self, app) -> None:
        """_run_scan passes the app-scoped checkpointer + rate limiter into the factory."""
        from migratowl.models.schemas import ScanWebhookPayload

        sentinel_cp = object()
        sentinel_rl = object()
        app.state.checkpointer = sentinel_cp
        app.state.rate_limiter = sentinel_rl

        with patch("migratowl.agent.factory.create_migratowl_agent") as mock_factory, \
             patch("migratowl.api.main.notify_pr_done", new_callable=AsyncMock), \
             patch("migratowl.api.main.notify_pr_start", new_callable=AsyncMock):
            mock_graph = AsyncMock()
            mock_graph.ainvoke.return_value = {"messages": []}
            mock_factory.return_value = mock_graph

            payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
            job = app.state.job_store.create(payload)

            await main_mod._run_scan(app, job.job_id)

            call_kwargs = mock_factory.call_args[1]
            assert call_kwargs["checkpointer"] is sentinel_cp
            assert call_kwargs["rate_limiter"] is sentinel_rl

    @pytest.mark.asyncio
    async def test_run_scan_persists_sandbox_id(self, app, fake_pipeline) -> None:
        """When the pipeline's tools acquire a sandbox, its id is persisted to the job store."""
        from migratowl.models.schemas import ScanWebhookPayload

        fake_pipeline.build_tools.side_effect = lambda *a, **kw: kw["on_sandbox_acquired"]("sbx-run") or MagicMock()

        with patch("migratowl.agent.factory.create_migratowl_agent") as mock_factory, \
             patch("migratowl.api.main.notify_pr_done", new_callable=AsyncMock), \
             patch("migratowl.api.main.notify_pr_start", new_callable=AsyncMock):
            mock_factory.return_value.ainvoke = AsyncMock(return_value=_ok_result())
            payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
            job = app.state.job_store.create(payload)

            await main_mod._run_scan(app, job.job_id)
            # call_soon_threadsafe callbacks run on the loop; yield to let them fire.
            await asyncio.sleep(0.01)

            assert app.state.job_store.get(job.job_id).sandbox_id == "sbx-run"


class TestResumeEndpoint:
    @pytest.mark.asyncio
    async def test_resume_404_for_unknown_job(self, client: AsyncClient) -> None:
        resp = await client.post("/jobs/nope/resume")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_resume_409_when_not_interrupted(self, app, client: AsyncClient) -> None:
        from migratowl.models.schemas import ScanWebhookPayload

        job = app.state.job_store.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
        resp = await client.post(f"/jobs/{job.job_id}/resume")  # PENDING, not INTERRUPTED
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_resume_accepts_interrupted_job(self, app, client: AsyncClient) -> None:
        from migratowl.models.schemas import JobState, ScanWebhookPayload

        job = app.state.job_store.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
        app.state.job_store.update_state(job.job_id, JobState.INTERRUPTED)

        with patch("migratowl.api.main._run_scan", new_callable=AsyncMock) as mock_run:
            resp = await client.post(f"/jobs/{job.job_id}/resume")
            await asyncio.sleep(0.01)

        assert resp.status_code == 202
        assert resp.json()["job_id"] == job.job_id
        mock_run.assert_awaited_once()
        assert mock_run.await_args.kwargs.get("resume") is True

    @pytest.mark.asyncio
    async def test_resume_increments_retry(self, app, client: AsyncClient) -> None:
        from migratowl.models.schemas import JobState, ScanWebhookPayload

        job = app.state.job_store.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
        app.state.job_store.update_state(job.job_id, JobState.INTERRUPTED)

        with patch("migratowl.api.main._run_scan", new_callable=AsyncMock):
            await client.post(f"/jobs/{job.job_id}/resume")

        assert app.state.job_store.get(job.job_id).retry_count == 1

    @pytest.mark.asyncio
    async def test_resume_over_cap_returns_409(self, app, client: AsyncClient) -> None:
        from migratowl.models.schemas import JobState, ScanWebhookPayload

        job = app.state.job_store.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
        for _ in range(app.state.settings.max_scan_retries):
            app.state.job_store.increment_retry(job.job_id)
        app.state.job_store.update_state(job.job_id, JobState.INTERRUPTED)

        with patch("migratowl.api.main._run_scan", new_callable=AsyncMock) as mock_run:
            resp = await client.post(f"/jobs/{job.job_id}/resume")

        assert resp.status_code == 409
        mock_run.assert_not_awaited()
        assert app.state.job_store.get(job.job_id).state == JobState.FAILED

    @pytest.mark.asyncio
    async def test_double_resume_one_wins(self, app, client: AsyncClient) -> None:
        """Two concurrent resume requests: exactly one 202, one 409."""
        from migratowl.models.schemas import JobState, ScanWebhookPayload

        job = app.state.job_store.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
        app.state.job_store.update_state(job.job_id, JobState.INTERRUPTED)

        with patch("migratowl.api.main._run_scan", new_callable=AsyncMock):
            r1, r2 = await asyncio.gather(
                client.post(f"/jobs/{job.job_id}/resume"),
                client.post(f"/jobs/{job.job_id}/resume"),
            )

        assert sorted([r1.status_code, r2.status_code]) == [202, 409]


class TestRunScanResumeGuards:
    @pytest.mark.asyncio
    async def test_resume_calls_reconnect_or_restart(self, app) -> None:
        from migratowl.models.schemas import JobState, ScanWebhookPayload

        app.state.checkpointer = MagicMock()
        job = app.state.job_store.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
        app.state.job_store.update_state(job.job_id, JobState.RUNNING)  # already claimed

        with (
            patch("migratowl.api.resume.reconnect_or_restart", new_callable=AsyncMock) as mock_rr,
            patch("migratowl.agent.factory.create_migratowl_agent") as mock_factory,
            patch("migratowl.api.main.notify_pr_done", new_callable=AsyncMock),
            patch("migratowl.api.main.notify_pr_start", new_callable=AsyncMock),
        ):
            graph = AsyncMock()
            graph.ainvoke.return_value = {"messages": []}
            mock_factory.return_value = graph
            await main_mod._run_scan(app, job.job_id, resume=True)

        mock_rr.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_side_effects_not_refired_when_already_done(self, app) -> None:
        """A resumed job whose side effects already fired must NOT post again."""
        from migratowl.models.schemas import JobState, ScanWebhookPayload

        app.state.checkpointer = MagicMock()
        job = app.state.job_store.create(
            ScanWebhookPayload(repo_url="https://github.com/x/y", pr_number=5)
        )
        app.state.job_store.mark_side_effects_done(job.job_id)
        app.state.job_store.update_state(job.job_id, JobState.RUNNING)

        with (
            patch("migratowl.api.resume.reconnect_or_restart", new_callable=AsyncMock),
            patch("migratowl.agent.factory.create_migratowl_agent") as mock_factory,
            patch("migratowl.api.main.notify_pr_start", new_callable=AsyncMock),
            patch("migratowl.api.main.notify_pr_done", new_callable=AsyncMock) as mock_done,
        ):
            graph = AsyncMock()
            graph.ainvoke.return_value = {"messages": []}
            mock_factory.return_value = graph
            await main_mod._run_scan(app, job.job_id, resume=True)

        mock_done.assert_not_awaited()


class TestListJobsEndpoint:
    @pytest.mark.asyncio
    async def test_requires_state_param(self, client: AsyncClient) -> None:
        resp = await client.get("/jobs")
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_state_returns_422(self, client: AsyncClient) -> None:
        resp = await client.get("/jobs?state=bogus")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_filters_by_state(self, app, client: AsyncClient) -> None:
        from migratowl.models.schemas import JobState, ScanWebhookPayload

        app.state.job_store.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
        b = app.state.job_store.create(ScanWebhookPayload(repo_url="https://github.com/x/z"))
        app.state.job_store.update_state(b.job_id, JobState.INTERRUPTED)

        resp = await client.get("/jobs?state=interrupted")
        assert resp.status_code == 200
        ids = [j["job_id"] for j in resp.json()["jobs"]]
        assert ids == [b.job_id]


class TestGracefulShutdown:
    @pytest.mark.asyncio
    async def test_running_jobs_marked_interrupted_on_shutdown(
        self, mock_manager: MagicMock
    ) -> None:
        from migratowl.models.schemas import JobState, ScanWebhookPayload

        settings = Settings(_env_file=None, persistence_backend="memory")
        application = main_mod.create_app(settings=settings, manager=mock_manager)
        async with application.router.lifespan_context(application):
            job = application.state.job_store.create(
                ScanWebhookPayload(repo_url="https://github.com/x/y")
            )
            application.state.job_store.update_state(job.job_id, JobState.RUNNING)
            job_id = job.job_id
        # After lifespan exit (shutdown), the running job is INTERRUPTED.
        assert application.state.job_store.get(job_id).state == JobState.INTERRUPTED

class TestSandboxReleaseOnTerminalState:
    """A finished job's sandbox must be deleted, not left running until shutdown."""

    @pytest.fixture
    def sandbox_manager(self, mock_manager: MagicMock) -> MagicMock:
        return mock_manager

    async def _run_job(self, client: AsyncClient, sandbox_manager: MagicMock, ainvoke: AsyncMock) -> str:
        async def fake_ainvoke(*args, **kwargs):
            # The sandbox is acquired lazily during the run, keyed by thread_id.
            thread_id = kwargs["config"]["configurable"]["thread_id"]
            sandbox_manager._sandbox_by_thread[thread_id] = MagicMock(id="sb-123")
            return await ainvoke(*args, **kwargs)

        with patch("migratowl.api.main.notify_pr_start"), \
             patch("migratowl.api.main.notify_pr_done"), \
             patch("migratowl.api.main.notify_pr_failed"), \
             patch("migratowl.agent.factory.create_migratowl_agent") as mock_agent:
            mock_agent.return_value.ainvoke = fake_ainvoke
            resp = await client.post("/webhook", json={"repo_url": "https://github.com/x/y"})
            await asyncio.sleep(0.05)
        return resp.json()["job_id"]

    async def test_sandbox_deleted_after_success(self, client: AsyncClient, sandbox_manager: MagicMock) -> None:
        job_id = await self._run_job(client, sandbox_manager, AsyncMock(return_value=_ok_result()))

        sandbox_manager._provider.adelete.assert_awaited_once_with(sandbox_id="sb-123")
        assert job_id not in sandbox_manager._sandbox_by_thread

    async def test_sandbox_deleted_after_failure(self, client: AsyncClient, sandbox_manager: MagicMock) -> None:
        job_id = await self._run_job(client, sandbox_manager, AsyncMock(side_effect=RuntimeError("boom")))

        sandbox_manager._provider.adelete.assert_awaited_once_with(sandbox_id="sb-123")
        assert job_id not in sandbox_manager._sandbox_by_thread

    async def test_delete_error_keeps_job_completed(self, client: AsyncClient, sandbox_manager: MagicMock) -> None:
        sandbox_manager._provider.adelete.side_effect = RuntimeError("api down")
        job_id = await self._run_job(client, sandbox_manager, AsyncMock(return_value=_ok_result()))

        job = (await client.get(f"/jobs/{job_id}")).json()
        assert job["state"] == "completed"


class TestRunScanWithoutReport:
    """A run that returns no structured report must fail, not complete with an empty report."""

    async def test_job_failed_with_clear_error(self, client: AsyncClient) -> None:
        with patch("migratowl.api.main.notify_pr_start"), \
             patch("migratowl.api.main.notify_pr_done") as mock_done, \
             patch("migratowl.api.main.notify_pr_failed") as mock_failed, \
             patch("migratowl.agent.factory.create_migratowl_agent") as mock_agent:
            mock_agent.return_value.ainvoke = AsyncMock(
                return_value={"messages": [{"role": "assistant", "content": "wrote report to /tmp/r.json"}]}
            )
            resp = await client.post("/webhook", json={"repo_url": "https://github.com/x/y"})
            await asyncio.sleep(0.05)

        job = (await client.get(f"/jobs/{resp.json()['job_id']}")).json()
        assert job["state"] == "failed"
        assert "structured report" in job["error"]
        mock_failed.assert_awaited_once()
        mock_done.assert_not_awaited()


class TestRunScanPipeline:
    async def test_no_llm_when_nothing_pending(self, app, fake_pipeline) -> None:
        from migratowl.models.schemas import ScanWebhookPayload

        fake_pipeline.prepare_scan.return_value = _prepared_scan(pending=False)
        with patch("migratowl.agent.factory.create_migratowl_agent") as mock_factory, \
             patch("migratowl.api.main.notify_pr_start", new_callable=AsyncMock), \
             patch("migratowl.api.main.notify_pr_done", new_callable=AsyncMock):
            job = app.state.job_store.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
            await main_mod._run_scan(app, job.job_id)

        mock_factory.assert_not_called()
        done = app.state.job_store.get(job.job_id)
        assert done.state == "completed"
        assert done.result.reports[0].dependency_name == "flask"
        assert done.result.reports[0].confidence == 1.0

    async def test_llm_gets_brief_and_report_is_assembled(self, app) -> None:
        from migratowl.models.schemas import ScanWebhookPayload

        with patch("migratowl.agent.factory.create_migratowl_agent") as mock_factory, \
             patch("migratowl.api.main.notify_pr_start", new_callable=AsyncMock), \
             patch("migratowl.api.main.notify_pr_done", new_callable=AsyncMock):
            mock_factory.return_value.ainvoke = AsyncMock(return_value=_ok_result())
            job = app.state.job_store.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
            await main_mod._run_scan(app, job.job_id)

        user_msg = mock_factory.return_value.ainvoke.await_args.args[0]["messages"][0][1]
        assert user_msg.startswith("Repository: https://github.com/x/y")
        assert "tools" in mock_factory.call_args.kwargs
        done = app.state.job_store.get(job.job_id)
        assert done.result.scan_result.manifests_found == ["pyproject.toml"]
        assert done.result.reports[0].is_breaking is True

    async def test_pipeline_error_fails_job_with_message(self, app, fake_pipeline) -> None:
        from migratowl.models.schemas import ScanWebhookPayload
        from migratowl.pipeline import PipelineError

        fake_pipeline.prepare_scan.side_effect = PipelineError("Failed to clone https://github.com/x/y")
        with patch("migratowl.api.main.notify_pr_start", new_callable=AsyncMock), \
             patch("migratowl.api.main.notify_pr_failed", new_callable=AsyncMock) as mock_failed:
            job = app.state.job_store.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
            await main_mod._run_scan(app, job.job_id)

        failed = app.state.job_store.get(job.job_id)
        assert failed.state == "failed"
        assert failed.error == "Failed to clone https://github.com/x/y"
        mock_failed.assert_awaited_once()
