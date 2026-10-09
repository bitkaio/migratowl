# SPDX-License-Identifier: Apache-2.0

"""Tests for the durable SQLite-backed JobStore."""

import pytest

from migratowl.api.sqlite_jobs import SqliteJobStore
from migratowl.models.schemas import (
    JobState,
    ScanAnalysisReport,
    ScanResult,
    ScanWebhookPayload,
)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "jobs.db")


@pytest.fixture
def store(db_path: str) -> SqliteJobStore:
    return SqliteJobStore(db_path)


@pytest.fixture
def payload() -> ScanWebhookPayload:
    return ScanWebhookPayload(repo_url="https://github.com/x/y")


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


class TestSqliteJobStoreBasics:
    def test_create_and_get(self, store: SqliteJobStore, payload: ScanWebhookPayload) -> None:
        created = store.create(payload)
        fetched = store.get(created.job_id)
        assert fetched is not None
        assert fetched.job_id == created.job_id
        assert fetched.state == JobState.PENDING
        assert fetched.payload == payload

    def test_get_missing_returns_none(self, store: SqliteJobStore) -> None:
        assert store.get("nope") is None

    def test_unique_ids(self, store: SqliteJobStore, payload: ScanWebhookPayload) -> None:
        assert store.create(payload).job_id != store.create(payload).job_id

    def test_update_state(self, store: SqliteJobStore, payload: ScanWebhookPayload) -> None:
        job = store.create(payload)
        store.update_state(job.job_id, JobState.RUNNING)
        assert store.get(job.job_id).state == JobState.RUNNING

    def test_update_missing_raises(self, store: SqliteJobStore) -> None:
        with pytest.raises(KeyError):
            store.update_state("nope", JobState.RUNNING)

    def test_set_error(self, store: SqliteJobStore, payload: ScanWebhookPayload) -> None:
        job = store.create(payload)
        store.set_error(job.job_id, "boom")
        got = store.get(job.job_id)
        assert got.state == JobState.FAILED
        assert got.error == "boom"

    def test_set_sandbox_id(self, store: SqliteJobStore, payload: ScanWebhookPayload) -> None:
        job = store.create(payload)
        store.set_sandbox_id(job.job_id, "sbx-1")
        assert store.get(job.job_id).sandbox_id == "sbx-1"
        store.set_sandbox_id(job.job_id, None)
        assert store.get(job.job_id).sandbox_id is None

    def test_increment_retry(self, store: SqliteJobStore, payload: ScanWebhookPayload) -> None:
        job = store.create(payload)
        assert store.increment_retry(job.job_id) == 1
        assert store.increment_retry(job.job_id) == 2
        assert store.get(job.job_id).retry_count == 2

    def test_list_by_state(self, store: SqliteJobStore, payload: ScanWebhookPayload) -> None:
        a = store.create(payload)
        b = store.create(payload)
        store.update_state(b.job_id, JobState.RUNNING)
        assert [j.job_id for j in store.list_by_state(JobState.PENDING)] == [a.job_id]
        assert [j.job_id for j in store.list_by_state(JobState.RUNNING)] == [b.job_id]

    def test_mark_side_effects_done(self, store: SqliteJobStore, payload: ScanWebhookPayload) -> None:
        job = store.create(payload)
        assert store.get(job.job_id).side_effects_done is False
        store.mark_side_effects_done(job.job_id)
        assert store.get(job.job_id).side_effects_done is True


class TestSqliteJobStoreResultRoundtrip:
    def test_result_roundtrips_pydantic(self, store: SqliteJobStore, payload: ScanWebhookPayload) -> None:
        job = store.create(payload)
        report = _report()
        store.set_result(job.job_id, report)
        got = store.get(job.job_id)
        assert got.state == JobState.COMPLETED
        assert got.result == report


class TestSqliteJobStoreDurability:
    def test_persists_across_new_store_instance(self, db_path: str, payload: ScanWebhookPayload) -> None:
        """The whole point of crash recovery: state survives a fresh process/store."""
        store1 = SqliteJobStore(db_path)
        job = store1.create(payload)
        store1.update_state(job.job_id, JobState.RUNNING)
        store1.set_sandbox_id(job.job_id, "sbx-persist")
        store1.close()

        store2 = SqliteJobStore(db_path)
        got = store2.get(job.job_id)
        assert got is not None
        assert got.state == JobState.RUNNING
        assert got.sandbox_id == "sbx-persist"


class TestSqliteJobStoreClaimForResume:
    def test_claim_is_atomic(self, store: SqliteJobStore, payload: ScanWebhookPayload) -> None:
        job = store.create(payload)
        store.update_state(job.job_id, JobState.INTERRUPTED)
        assert store.claim_for_resume(job.job_id) is True
        assert store.claim_for_resume(job.job_id) is False
        assert store.get(job.job_id).state == JobState.RUNNING

    def test_claim_non_interrupted_returns_false(self, store: SqliteJobStore, payload: ScanWebhookPayload) -> None:
        job = store.create(payload)  # PENDING
        assert store.claim_for_resume(job.job_id) is False


class TestCreateJobStoreSqlite:
    def test_factory_returns_sqlite_for_sqlite_backend(self, db_path: str) -> None:
        from migratowl.api.jobs import create_job_store
        from migratowl.config import Settings

        settings = Settings(_env_file=None, persistence_backend="sqlite", jobs_db_path=db_path)
        store = create_job_store(settings)
        assert isinstance(store, SqliteJobStore)


def test_database_from_0_6_with_lease_columns_still_loads(db_path: str, payload: ScanWebhookPayload) -> None:
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE jobs (job_id TEXT PRIMARY KEY, state TEXT NOT NULL, created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, payload_json TEXT NOT NULL, result_json TEXT, error TEXT, sandbox_id TEXT, "
        "retry_count INTEGER NOT NULL DEFAULT 0, side_effects_done INTEGER NOT NULL DEFAULT 0, "
        "owner_pid INTEGER, heartbeat_at TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs VALUES ('j1', 'interrupted', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', "
        "?, NULL, NULL, 'sb-1', 1, 0, 4242, '2026-01-01T00:00:00+00:00')",
        (payload.model_dump_json(),),
    )
    conn.commit()
    conn.close()

    store = SqliteJobStore(db_path)
    job = store.get("j1")
    assert job is not None and job.state == JobState.INTERRUPTED and job.sandbox_id == "sb-1"
    assert store.create(payload).job_id
