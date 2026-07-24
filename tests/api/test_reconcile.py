# SPDX-License-Identifier: Apache-2.0

"""Tests for the startup orphan-job reconciler."""

import pytest

from migratowl.api.jobs import InMemoryJobStore
from migratowl.api.reconcile import reconcile_orphans
from migratowl.config import Settings
from migratowl.models.schemas import JobState, ScanWebhookPayload


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, max_scan_retries=3)


@pytest.fixture
def payload() -> ScanWebhookPayload:
    return ScanWebhookPayload(repo_url="https://github.com/x/y")


def _seed(store: InMemoryJobStore, payload: ScanWebhookPayload, state: JobState, retry: int = 0):
    job = store.create(payload)
    for _ in range(retry):
        store.increment_retry(job.job_id)
    if state != JobState.PENDING:
        store.update_state(job.job_id, state)
    return job


class TestReconcileOrphans:
    def test_running_job_becomes_interrupted(self, store, settings, payload) -> None:
        job = _seed(store, payload, JobState.RUNNING)
        reconcile_orphans(store, settings)
        assert store.get(job.job_id).state == JobState.INTERRUPTED

    def test_pending_job_becomes_interrupted(self, store, settings, payload) -> None:
        """A job orphaned while queued behind the semaphore never started — resume
        is a clean fresh run."""
        job = _seed(store, payload, JobState.PENDING)
        reconcile_orphans(store, settings)
        assert store.get(job.job_id).state == JobState.INTERRUPTED

    def test_over_retry_cap_becomes_failed(self, store, settings, payload) -> None:
        job = _seed(store, payload, JobState.RUNNING, retry=3)
        reconcile_orphans(store, settings)
        got = store.get(job.job_id)
        assert got.state == JobState.FAILED
        assert got.error is not None

    def test_completed_untouched(self, store, settings, payload) -> None:
        job = _seed(store, payload, JobState.COMPLETED)
        reconcile_orphans(store, settings)
        assert store.get(job.job_id).state == JobState.COMPLETED

    def test_failed_untouched(self, store, settings, payload) -> None:
        job = _seed(store, payload, JobState.FAILED)
        reconcile_orphans(store, settings)
        assert store.get(job.job_id).state == JobState.FAILED

    def test_already_interrupted_untouched(self, store, settings, payload) -> None:
        job = _seed(store, payload, JobState.INTERRUPTED)
        reconcile_orphans(store, settings)
        assert store.get(job.job_id).state == JobState.INTERRUPTED

    def test_empty_store_is_noop(self, store, settings) -> None:
        reconcile_orphans(store, settings)  # no jobs — must not raise
