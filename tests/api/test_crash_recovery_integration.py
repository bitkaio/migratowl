# SPDX-License-Identifier: Apache-2.0

"""Integration tests for crash recovery against real SQLite (no store mocks).

Simulates a process crash by tearing down one store/checkpointer and opening a
fresh pair on the SAME database files — the way a restarted process would — then
verifies reconcile + resume behave correctly on genuinely persisted state.
"""

import pytest

from migratowl.api.checkpoint import create_checkpointer
from migratowl.api.jobs import create_job_store
from migratowl.api.reconcile import reconcile_orphans
from migratowl.api.sqlite_jobs import SqliteJobStore
from migratowl.config import Settings
from migratowl.models.schemas import (
    JobState,
    ScanAnalysisReport,
    ScanResult,
    ScanWebhookPayload,
)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        persistence_backend="sqlite",
        jobs_db_path=str(tmp_path / "jobs.db"),
        checkpoint_db_path=str(tmp_path / "cp.db"),
        max_scan_retries=3,
    )


def _report() -> ScanAnalysisReport:
    return ScanAnalysisReport(
        repo_url="https://github.com/x/y",
        branch_name="main",
        scan_result=ScanResult(
            all_deps=[], outdated=[], manifests_found=[], scan_duration_seconds=0.0
        ),
        reports=[],
        total_duration_seconds=1.0,
    )


class TestCrashThenReconcileThenResume:
    def test_running_job_survives_crash_and_becomes_interrupted(self, settings) -> None:
        # --- process 1: a scan is running when the process dies ---
        store1 = create_job_store(settings)
        assert isinstance(store1, SqliteJobStore)
        job = store1.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
        store1.update_state(job.job_id, JobState.RUNNING)
        store1.set_sandbox_id(job.job_id, "sbx-live")
        store1.close()  # simulate crash (no graceful shutdown)

        # --- process 2: restart — fresh store on the same file ---
        store2 = create_job_store(settings)
        recovered = store2.get(job.job_id)
        assert recovered is not None
        assert recovered.state == JobState.RUNNING  # survived the crash
        assert recovered.sandbox_id == "sbx-live"

        # reconcile flips the orphaned RUNNING job to INTERRUPTED
        reconcile_orphans(store2, settings)
        assert store2.get(job.job_id).state == JobState.INTERRUPTED
        store2.close()

    def test_resume_claim_and_complete_across_restart(self, settings) -> None:
        store1 = create_job_store(settings)
        job = store1.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
        store1.update_state(job.job_id, JobState.RUNNING)
        store1.close()

        # restart + reconcile
        store2 = create_job_store(settings)
        reconcile_orphans(store2, settings)
        assert store2.get(job.job_id).state == JobState.INTERRUPTED

        # resume: atomic claim, then increment retry, then complete
        assert store2.claim_for_resume(job.job_id) is True
        assert store2.claim_for_resume(job.job_id) is False  # no double-claim
        assert store2.increment_retry(job.job_id) == 1
        store2.set_result(job.job_id, _report())
        done = store2.get(job.job_id)
        assert done.state == JobState.COMPLETED
        assert done.result is not None
        assert done.retry_count == 1
        store2.close()

    def test_retry_cap_across_repeated_crashes(self, settings) -> None:
        """A job that keeps crashing eventually fails instead of resuming forever."""
        store = create_job_store(settings)
        job = store.create(ScanWebhookPayload(repo_url="https://github.com/x/y"))
        # Simulate having already retried up to the cap.
        for _ in range(settings.max_scan_retries):
            store.increment_retry(job.job_id)
        store.update_state(job.job_id, JobState.RUNNING)

        reconcile_orphans(store, settings)
        got = store.get(job.job_id)
        assert got.state == JobState.FAILED
        assert got.error is not None
        store.close()

    @pytest.mark.asyncio
    async def test_checkpointer_persists_across_restart(self, settings) -> None:
        """A checkpoint written by one process is visible to a fresh one."""
        config = {"configurable": {"thread_id": "job-xyz", "checkpoint_ns": ""}}
        checkpoint = {
            "v": 1,
            "id": "ckpt-1",
            "ts": "2026-07-24T00:00:00+00:00",
            "channel_values": {"messages": []},
            "channel_versions": {},
            "versions_seen": {},
        }

        # process 1: write a checkpoint, then "crash"
        async with create_checkpointer(settings) as saver1:
            await saver1.aput(config, checkpoint, {}, {})

        # process 2: fresh checkpointer on the same file sees it
        async with create_checkpointer(settings) as saver2:
            loaded = await saver2.aget(config)
            assert loaded is not None
            assert loaded["id"] == "ckpt-1"

    @pytest.mark.asyncio
    async def test_checkpoint_thread_deleted_on_restart_path(self, settings) -> None:
        """The restart branch clears the checkpoint so the agent re-runs fresh."""
        config = {"configurable": {"thread_id": "job-del", "checkpoint_ns": ""}}
        checkpoint = {
            "v": 1,
            "id": "ckpt-del",
            "ts": "2026-07-24T00:00:00+00:00",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
        }
        async with create_checkpointer(settings) as saver:
            await saver.aput(config, checkpoint, {}, {})
            assert await saver.aget(config) is not None
            await saver.adelete_thread("job-del")
            assert await saver.aget(config) is None
