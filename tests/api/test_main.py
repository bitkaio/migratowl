"""Tests for FastAPI webhook app."""

import asyncio
from unittest.mock import MagicMock

import httpx
import pytest
from httpx import ASGITransport

from migratowl.api.jobs import JobStore
from migratowl.api.main import create_app
from migratowl.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def mock_sandbox() -> MagicMock:
    sandbox = MagicMock()
    sandbox.id = "sandbox-test-123"
    return sandbox


@pytest.fixture
def mock_provider() -> MagicMock:
    return MagicMock()


@pytest.fixture
def app(settings: Settings, mock_sandbox: MagicMock, mock_provider: MagicMock):
    """Create app with pre-initialized sandbox (skip lifespan K8s init)."""
    application = create_app(
        settings=settings, sandbox=mock_sandbox, provider=mock_provider
    )
    # Manually set state that lifespan would set (ASGITransport doesn't trigger lifespan)
    application.state.sandbox = mock_sandbox
    application.state.provider = mock_provider
    application.state.job_store = JobStore()
    application.state.settings = settings
    return application


@pytest.fixture
async def client(app) -> httpx.AsyncClient:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthz:
    @pytest.mark.asyncio
    async def test_returns_ok(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestWebhook:
    @pytest.mark.asyncio
    async def test_accepts_valid_payload(self, client: httpx.AsyncClient) -> None:
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
    async def test_rejects_missing_repo_url(self, client: httpx.AsyncClient) -> None:
        resp = await client.post("/webhook", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_returns_unique_job_ids(self, client: httpx.AsyncClient) -> None:
        resp1 = await client.post(
            "/webhook", json={"repo_url": "https://github.com/x/y"}
        )
        resp2 = await client.post(
            "/webhook", json={"repo_url": "https://github.com/x/y"}
        )
        assert resp1.json()["job_id"] != resp2.json()["job_id"]


class TestGetJob:
    @pytest.mark.asyncio
    async def test_returns_job_after_webhook(self, client: httpx.AsyncClient) -> None:
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
    async def test_returns_404_for_unknown_job(self, client: httpx.AsyncClient) -> None:
        resp = await client.get("/jobs/nonexistent-id")
        assert resp.status_code == 404
