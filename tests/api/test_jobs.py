# SPDX-License-Identifier: Apache-2.0

"""Tests for in-memory JobStore."""

import pytest

from migratowl.api.jobs import InMemoryJobStore, create_job_store
from migratowl.config import Settings
from migratowl.models.schemas import (
    JobState,
    ScanAnalysisReport,
    ScanResult,
    ScanWebhookPayload,
)


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def payload() -> ScanWebhookPayload:
    return ScanWebhookPayload(repo_url="https://github.com/x/y")


class TestJobStoreCreate:
    def test_creates_pending_job(self, store: InMemoryJobStore, payload: ScanWebhookPayload) -> None:
        status = store.create(payload)
        assert status.state == JobState.PENDING
        assert status.payload == payload
        assert status.job_id  # non-empty

    def test_unique_ids(self, store: InMemoryJobStore, payload: ScanWebhookPayload) -> None:
        s1 = store.create(payload)
        s2 = store.create(payload)
        assert s1.job_id != s2.job_id


class TestJobStoreGet:
    def test_get_existing(self, store: InMemoryJobStore, payload: ScanWebhookPayload) -> None:
        created = store.create(payload)
        fetched = store.get(created.job_id)
        assert fetched is not None
        assert fetched.job_id == created.job_id

    def test_get_missing_returns_none(self, store: InMemoryJobStore) -> None:
        assert store.get("nonexistent") is None


class TestJobStoreUpdateState:
    def test_update_to_running(self, store: InMemoryJobStore, payload: ScanWebhookPayload) -> None:
        status = store.create(payload)
        store.update_state(status.job_id, JobState.RUNNING)
        updated = store.get(status.job_id)
        assert updated is not None
        assert updated.state == JobState.RUNNING
        assert updated.updated_at >= status.created_at

    def test_update_missing_raises(self, store: InMemoryJobStore) -> None:
        with pytest.raises(KeyError):
            store.update_state("nonexistent", JobState.RUNNING)


class TestJobStoreSetResult:
    def test_set_result(self, store: InMemoryJobStore, payload: ScanWebhookPayload) -> None:
        status = store.create(payload)
        report = ScanAnalysisReport(
            repo_url="https://github.com/x/y",
            branch_name="main",
            scan_result=ScanResult(
                all_deps=[], outdated=[], manifests_found=[], scan_duration_seconds=0.0
            ),
            reports=[],
            total_duration_seconds=1.0,
        )
        store.set_result(status.job_id, report)
        updated = store.get(status.job_id)
        assert updated is not None
        assert updated.state == JobState.COMPLETED
        assert updated.result == report

    def test_set_result_missing_raises(self, store: InMemoryJobStore) -> None:
        report = ScanAnalysisReport(
            repo_url="https://github.com/x/y",
            branch_name="main",
            scan_result=ScanResult(
                all_deps=[], outdated=[], manifests_found=[], scan_duration_seconds=0.0
            ),
            reports=[],
            total_duration_seconds=1.0,
        )
        with pytest.raises(KeyError):
            store.set_result("nonexistent", report)


class TestJobStoreSetError:
    def test_set_error(self, store: InMemoryJobStore, payload: ScanWebhookPayload) -> None:
        status = store.create(payload)
        store.set_error(status.job_id, "Sandbox crashed")
        updated = store.get(status.job_id)
        assert updated is not None
        assert updated.state == JobState.FAILED
        assert updated.error == "Sandbox crashed"

    def test_set_error_missing_raises(self, store: InMemoryJobStore) -> None:
        with pytest.raises(KeyError):
            store.set_error("nonexistent", "boom")


class TestJobStoreSetSandboxId:
    def test_set_sandbox_id(self, store: InMemoryJobStore, payload: ScanWebhookPayload) -> None:
        status = store.create(payload)
        store.set_sandbox_id(status.job_id, "sbx-42")
        assert store.get(status.job_id).sandbox_id == "sbx-42"

    def test_clear_sandbox_id(self, store: InMemoryJobStore, payload: ScanWebhookPayload) -> None:
        status = store.create(payload)
        store.set_sandbox_id(status.job_id, "sbx-42")
        store.set_sandbox_id(status.job_id, None)
        assert store.get(status.job_id).sandbox_id is None

    def test_missing_raises(self, store: InMemoryJobStore) -> None:
        with pytest.raises(KeyError):
            store.set_sandbox_id("nope", "sbx")


class TestJobStoreIncrementRetry:
    def test_increment_returns_new_count(self, store: InMemoryJobStore, payload: ScanWebhookPayload) -> None:
        status = store.create(payload)
        assert store.increment_retry(status.job_id) == 1
        assert store.increment_retry(status.job_id) == 2
        assert store.get(status.job_id).retry_count == 2

    def test_missing_raises(self, store: InMemoryJobStore) -> None:
        with pytest.raises(KeyError):
            store.increment_retry("nope")


class TestJobStoreListByState:
    def test_filters_by_state(self, store: InMemoryJobStore, payload: ScanWebhookPayload) -> None:
        a = store.create(payload)
        b = store.create(payload)
        store.update_state(b.job_id, JobState.RUNNING)
        pending = store.list_by_state(JobState.PENDING)
        running = store.list_by_state(JobState.RUNNING)
        assert [j.job_id for j in pending] == [a.job_id]
        assert [j.job_id for j in running] == [b.job_id]

    def test_empty_when_none_match(self, store: InMemoryJobStore) -> None:
        assert store.list_by_state(JobState.INTERRUPTED) == []


class TestJobStoreMarkSideEffectsDone:
    def test_marks_done(self, store: InMemoryJobStore, payload: ScanWebhookPayload) -> None:
        status = store.create(payload)
        assert store.get(status.job_id).side_effects_done is False
        store.mark_side_effects_done(status.job_id)
        assert store.get(status.job_id).side_effects_done is True

    def test_missing_raises(self, store: InMemoryJobStore) -> None:
        with pytest.raises(KeyError):
            store.mark_side_effects_done("nope")


class TestJobStoreClaimForResume:
    def test_claim_transitions_interrupted_to_running(
        self, store: InMemoryJobStore, payload: ScanWebhookPayload
    ) -> None:
        status = store.create(payload)
        store.update_state(status.job_id, JobState.INTERRUPTED)
        assert store.claim_for_resume(status.job_id) is True
        assert store.get(status.job_id).state == JobState.RUNNING

    def test_second_claim_returns_false(
        self, store: InMemoryJobStore, payload: ScanWebhookPayload
    ) -> None:
        status = store.create(payload)
        store.update_state(status.job_id, JobState.INTERRUPTED)
        assert store.claim_for_resume(status.job_id) is True
        assert store.claim_for_resume(status.job_id) is False

    def test_claim_non_interrupted_returns_false(
        self, store: InMemoryJobStore, payload: ScanWebhookPayload
    ) -> None:
        status = store.create(payload)  # PENDING
        assert store.claim_for_resume(status.job_id) is False

    def test_claim_missing_returns_false(self, store: InMemoryJobStore) -> None:
        assert store.claim_for_resume("nope") is False


class TestCreateJobStore:
    def test_memory_backend_returns_inmemory(self) -> None:
        settings = Settings(_env_file=None, persistence_backend="memory")
        store = create_job_store(settings)
        assert isinstance(store, InMemoryJobStore)