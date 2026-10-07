# SPDX-License-Identifier: Apache-2.0

"""API authentication and callback_url SSRF protection."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

import migratowl.api.main as main_mod
from migratowl.api.jobs import InMemoryJobStore
from migratowl.config import Settings

TOKEN = "s3cret-token"


def _app(**settings_kwargs):
    settings = Settings(_env_file=None, **settings_kwargs)
    application = main_mod.create_app(settings=settings, manager=MagicMock())
    application.state.manager = MagicMock()
    application.state.job_store = InMemoryJobStore()
    application.state.settings = settings
    application.state.scan_semaphore = asyncio.Semaphore(1)
    application.state.tasks = set()
    return application


@pytest.fixture(autouse=True)
def no_scans():
    with patch("migratowl.api.main._run_scan", new_callable=AsyncMock):
        yield


async def _client(application) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=application), base_url="http://test")


class TestApiToken:
    @pytest.mark.asyncio
    async def test_open_when_no_token_configured(self) -> None:
        async with await _client(_app()) as c:
            resp = await c.post("/webhook", json={"repo_url": "https://github.com/o/r"})
        assert resp.status_code == 202

    @pytest.mark.asyncio
    @pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
    async def test_webhook_rejects_missing_or_wrong_token(self, headers: dict) -> None:
        async with await _client(_app(api_token=TOKEN)) as c:
            resp = await c.post("/webhook", json={"repo_url": "https://github.com/o/r"}, headers=headers)
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_token_grants_access_to_webhook_and_jobs(self) -> None:
        auth = {"Authorization": f"Bearer {TOKEN}"}
        async with await _client(_app(api_token=TOKEN)) as c:
            created = await c.post("/webhook", json={"repo_url": "https://github.com/o/r"}, headers=auth)
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            assert (await c.get(f"/jobs/{job_id}", headers=auth)).status_code == 200
            assert (await c.get(f"/jobs/{job_id}")).status_code == 401
            assert (await c.get("/jobs?state=pending")).status_code == 401
            assert (await c.post(f"/jobs/{job_id}/resume")).status_code == 401

    @pytest.mark.asyncio
    async def test_healthz_stays_open(self) -> None:
        async with await _client(_app(api_token=TOKEN)) as c:
            assert (await c.get("/healthz")).status_code == 200


class TestCallbackUrlValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", [
        "ftp://example.com/cb",
        "file:///etc/passwd",
        "http://127.0.0.1:8000/cb",
        "http://localhost/cb",
        "http://10.1.2.3/cb",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/cb",
        "https://192.168.1.10/cb",
    ])
    async def test_rejects_non_http_and_internal_targets(self, url: str) -> None:
        async with await _client(_app()) as c:
            resp = await c.post("/webhook", json={"repo_url": "https://github.com/o/r", "callback_url": url})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_accepts_public_https_callback(self) -> None:
        async with await _client(_app()) as c:
            resp = await c.post(
                "/webhook", json={"repo_url": "https://github.com/o/r", "callback_url": "https://hooks.example.com/cb"}
            )
        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_private_targets_allowed_when_opted_in(self) -> None:
        async with await _client(_app(callback_allow_private=True)) as c:
            resp = await c.post(
                "/webhook", json={"repo_url": "https://github.com/o/r", "callback_url": "http://localhost:9000/cb"}
            )
        assert resp.status_code == 202


class TestCallbackDelivery:
    @pytest.mark.asyncio
    async def test_hostname_resolving_to_private_address_is_not_called(self, monkeypatch) -> None:
        # DNS can point a public-looking name at an internal address.
        calls: list = []
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200)))

        async def fake_resolve(host: str) -> list[str]:
            return ["10.0.0.7"]

        monkeypatch.setattr(main_mod, "_resolve_host", fake_resolve)
        with patch("migratowl.http.get_http_client", return_value=client):
            await main_mod._post_callback("https://evil.example.com/cb", "j1", {}, state="failed")
        await client.aclose()
        assert calls == []

    @pytest.mark.asyncio
    async def test_redirects_are_not_followed(self, monkeypatch) -> None:
        calls: list = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)

        async def fake_resolve(host: str) -> list[str]:
            return ["93.184.216.34"]

        monkeypatch.setattr(main_mod, "_resolve_host", fake_resolve)
        with patch("migratowl.http.get_http_client", return_value=client):
            await main_mod._post_callback("https://hooks.example.com/cb", "j1", {}, state="completed")
        await client.aclose()
        assert calls == ["https://hooks.example.com/cb"]
