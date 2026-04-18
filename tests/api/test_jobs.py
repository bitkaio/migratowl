# SPDX-License-Identifier: Apache-2.0

"""Tests for in-memory JobStore."""

import pytest

from migratowl.api.jobs import JobStore
from migratowl.models.schemas import (
    JobState,
    ScanAnalysisReport,
    ScanResult,
    ScanWebhookPayload,
)


@pytest.fixture
def store() -> JobStore:
    return JobStore()


@pytest.fixture
def payload() -> ScanWebhookPayload:
    return ScanWebhookPayload(repo_url="https://github.com/x/y")


class TestJobStoreCreate:
    def test_creates_pending_job(self, store: JobStore, payload: ScanWebhookPayload) -> None:
        status = store.create(payload)
        assert status.state == JobState.PENDING
        assert status.payload == payload
        assert status.job_id  # non-empty

    def test_unique_ids(self, store: JobStore, payload: ScanWebhookPayload) -> None:
        s1 = store.create(payload)
        s2 = store.create(payload)
        assert s1.job_id != s2.job_id


class TestJobStoreGet:
    def test_get_existing(self, store: JobStore, payload: ScanWebhookPayload) -> None:
        created = store.create(payload)
        fetched = store.get(created.job_id)
        assert fetched is not None
        assert fetched.job_id == created.job_id

    def test_get_missing_returns_none(self, store: JobStore) -> None:
        assert store.get("nonexistent") is None


class TestJobStoreUpdateState:
    def test_update_to_running(self, store: JobStore, payload: ScanWebhookPayload) -> None:
        status = store.create(payload)
        store.update_state(status.job_id, JobState.RUNNING)
        updated = store.get(status.job_id)
        assert updated is not None
        assert updated.state == JobState.RUNNING
        assert updated.updated_at >= status.created_at

    def test_update_missing_raises(self, store: JobStore) -> None:
        with pytest.raises(KeyError):
            store.update_state("nonexistent", JobState.RUNNING)


class TestJobStoreSetResult:
    def test_set_result(self, store: JobStore, payload: ScanWebhookPayload) -> None:
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

    def test_set_result_missing_raises(self, store: JobStore) -> None:
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
    def test_set_error(self, store: JobStore, payload: ScanWebhookPayload) -> None:
        status = store.create(payload)
        store.set_error(status.job_id, "Sandbox crashed")
        updated = store.get(status.job_id)
        assert updated is not None
        assert updated.state == JobState.FAILED
        assert updated.error == "Sandbox crashed"

    def test_set_error_missing_raises(self, store: JobStore) -> None:
        with pytest.raises(KeyError):
            store.set_error("nonexistent", "boom")