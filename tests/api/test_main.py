"""Tests for FastAPI webhook app."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import migratowl.api.main as main_mod
from httpx import AsyncClient, ASGITransport

from migratowl.api.jobs import JobStore
from migratowl.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def mock_manager() -> MagicMock:
    from langchain_kubernetes import KubernetesSandboxManager

    return MagicMock(spec=KubernetesSandboxManager)


@pytest.fixture
def app(settings: Settings, mock_manager: MagicMock):
    """Create app with pre-initialized manager (skip lifespan K8s init)."""
    application = main_mod.create_app(settings=settings, manager=mock_manager)
    # Manually set state that lifespan would set (ASGITransport doesn't trigger lifespan)
    application.state.manager = mock_manager
    application.state.job_store = JobStore()
    application.state.settings = settings
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
        assert data["state"] in ("pending", "running", "completed", "failed")

    @pytest.mark.asyncio
    async def test_returns_404_for_unknown_job(self, client: AsyncClient) -> None:
        resp = await client.get("/jobs/nonexistent-id")
        assert resp.status_code == 404


class TestWebhookNotifyIntegration:
    @pytest.mark.asyncio
    async def test_notify_pr_start_called_when_pr_and_sha_provided(
        self, app, client: AsyncClient
    ) -> None:
        main_mod._scan_semaphore = asyncio.Semaphore(1)
        with patch("migratowl.api.main.notify_pr_start") as mock_start, \
             patch("migratowl.api.main.notify_pr_done"), \
             patch("migratowl.agent.factory.create_migratowl_agent") as mock_agent:
            mock_agent.return_value.ainvoke = AsyncMock(return_value={"messages": []})
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
        main_mod._scan_semaphore = asyncio.Semaphore(1)
        with patch("migratowl.api.main.notify_pr_start"), \
             patch("migratowl.api.main.notify_pr_done") as mock_done, \
             patch("migratowl.agent.factory.create_migratowl_agent") as mock_agent:
            mock_agent.return_value.ainvoke = AsyncMock(return_value={"messages": []})

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
        main_mod._scan_semaphore = asyncio.Semaphore(1)
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
