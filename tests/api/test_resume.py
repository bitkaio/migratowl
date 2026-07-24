# SPDX-License-Identifier: Apache-2.0

"""Tests for resume reconnect-or-restart logic."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from migratowl.api.jobs import InMemoryJobStore
from migratowl.api.resume import reconnect_or_restart
from migratowl.models.schemas import JobState, ScanWebhookPayload


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def payload() -> ScanWebhookPayload:
    return ScanWebhookPayload(repo_url="https://github.com/x/y")


def _mock_manager() -> MagicMock:
    from langchain_kubernetes import KubernetesSandboxManager

    mgr = MagicMock(spec=KubernetesSandboxManager)
    mgr._provider = MagicMock()
    mgr._sandbox_by_thread = {}
    return mgr


class TestReconnectOrRestart:
    @pytest.mark.asyncio
    async def test_live_reconnect_seeds_cache(self, store, payload) -> None:
        job = store.create(payload)
        store.set_sandbox_id(job.job_id, "sbx-live")
        job = store.get(job.job_id)

        sandbox = MagicMock()
        sandbox.id = "sbx-live"
        sandbox.aexecute = AsyncMock(return_value=MagicMock(exit_code=0))
        manager = _mock_manager()
        manager._provider.areconnect = AsyncMock(return_value=sandbox)
        checkpointer = MagicMock()
        checkpointer.adelete_thread = AsyncMock()

        outcome = await reconnect_or_restart(manager, job, store, checkpointer)

        assert outcome == "reconnected"
        assert manager._sandbox_by_thread[job.job_id] is sandbox
        checkpointer.adelete_thread.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sandbox_gone_restarts(self, store, payload) -> None:
        from langchain_kubernetes import SandboxNotFoundError

        job = store.create(payload)
        store.set_sandbox_id(job.job_id, "sbx-gone")
        job = store.get(job.job_id)

        manager = _mock_manager()
        manager._provider.areconnect = AsyncMock(side_effect=SandboxNotFoundError("gone"))
        checkpointer = MagicMock()
        checkpointer.adelete_thread = AsyncMock()

        outcome = await reconnect_or_restart(manager, job, store, checkpointer)

        assert outcome == "restarted"
        checkpointer.adelete_thread.assert_awaited_once_with(job.job_id)
        assert store.get(job.job_id).sandbox_id is None

    @pytest.mark.asyncio
    async def test_optimistic_reconnect_not_live_restarts(self, store, payload) -> None:
        """agent-sandbox reconnect can return a backend without a live pod; a failing
        liveness probe must fall back to restart."""
        job = store.create(payload)
        store.set_sandbox_id(job.job_id, "sbx-dead")
        job = store.get(job.job_id)

        sandbox = MagicMock()
        sandbox.aexecute = AsyncMock(side_effect=RuntimeError("no pod"))
        manager = _mock_manager()
        manager._provider.areconnect = AsyncMock(return_value=sandbox)
        checkpointer = MagicMock()
        checkpointer.adelete_thread = AsyncMock()

        outcome = await reconnect_or_restart(manager, job, store, checkpointer)

        assert outcome == "restarted"
        checkpointer.adelete_thread.assert_awaited_once_with(job.job_id)

    @pytest.mark.asyncio
    async def test_no_sandbox_id_restarts(self, store, payload) -> None:
        """A job that crashed before acquiring a sandbox just restarts fresh."""
        job = store.create(payload)  # sandbox_id is None
        manager = _mock_manager()
        manager._provider.areconnect = AsyncMock()
        checkpointer = MagicMock()
        checkpointer.adelete_thread = AsyncMock()

        outcome = await reconnect_or_restart(manager, job, store, checkpointer)

        assert outcome == "restarted"
        manager._provider.areconnect.assert_not_awaited()
        checkpointer.adelete_thread.assert_awaited_once_with(job.job_id)
