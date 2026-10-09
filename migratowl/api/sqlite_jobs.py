# Copyright bitkaio LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Durable SQLite-backed JobStore for crash recovery.

Uses the stdlib ``sqlite3`` module (synchronous) behind the sync ``JobStore``
Protocol. Writes are sub-millisecond, so blocking the event loop briefly is
acceptable at scan volume; the pluggable seam lets us swap to an async driver
later if contention ever appears. Composite Pydantic fields (payload, result)
are stored as JSON TEXT columns.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from uuid import uuid4

from migratowl.models.schemas import (
    JobState,
    JobStatus,
    ScanAnalysisReport,
    ScanWebhookPayload,
)

# Bump when the table shape changes (no migrations — recreate the DB).
_SCHEMA_VERSION = 1

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    sandbox_id TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    side_effects_done INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);"


class SqliteJobStore:
    """Durable job store backed by a single SQLite file."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._lock = threading.Lock()
        # check_same_thread=False: background scan tasks and request handlers may
        # touch the store from different threads; the lock serializes writes.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION};")
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_INDEX)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_status(row: sqlite3.Row) -> JobStatus:
        return JobStatus(
            job_id=row["job_id"],
            state=JobState(row["state"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            payload=ScanWebhookPayload.model_validate_json(row["payload_json"]),
            result=(
                ScanAnalysisReport.model_validate_json(row["result_json"])
                if row["result_json"]
                else None
            ),
            error=row["error"],
            sandbox_id=row["sandbox_id"],
            retry_count=row["retry_count"],
            side_effects_done=bool(row["side_effects_done"]),
        )

    # ------------------------------------------------------------------
    # JobStore protocol
    # ------------------------------------------------------------------

    def create(self, payload: ScanWebhookPayload) -> JobStatus:
        job_id = str(uuid4())
        status = JobStatus(job_id=job_id, state=JobState.PENDING, payload=payload)
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (job_id, state, created_at, updated_at, payload_json, "
                "retry_count, side_effects_done) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    status.job_id,
                    status.state.value,
                    status.created_at.isoformat(),
                    status.updated_at.isoformat(),
                    payload.model_dump_json(),
                    0,
                    0,
                ),
            )
            self._conn.commit()
        return status

    def get(self, job_id: str) -> JobStatus | None:
        cur = self._conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        row = cur.fetchone()
        return self._row_to_status(row) if row is not None else None

    def update_state(self, job_id: str, state: JobState) -> None:
        self._update(job_id, "state = ?", (state.value,))

    def set_result(self, job_id: str, result: ScanAnalysisReport) -> None:
        self._update(
            job_id,
            "state = ?, result_json = ?",
            (JobState.COMPLETED.value, result.model_dump_json()),
        )

    def set_error(self, job_id: str, error: str) -> None:
        self._update(job_id, "state = ?, error = ?", (JobState.FAILED.value, error))

    def set_sandbox_id(self, job_id: str, sandbox_id: str | None) -> None:
        self._update(job_id, "sandbox_id = ?", (sandbox_id,))

    def increment_retry(self, job_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET retry_count = retry_count + 1, updated_at = ? "
                "WHERE job_id = ?",
                (datetime.now(UTC).isoformat(), job_id),
            )
            if cur.rowcount == 0:
                raise KeyError(job_id)
            self._conn.commit()
            row = self._conn.execute(
                "SELECT retry_count FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return int(row["retry_count"])

    def list_by_state(self, state: JobState) -> list[JobStatus]:
        cur = self._conn.execute(
            "SELECT * FROM jobs WHERE state = ? ORDER BY created_at", (state.value,)
        )
        return [self._row_to_status(r) for r in cur.fetchall()]

    def mark_side_effects_done(self, job_id: str) -> None:
        self._update(job_id, "side_effects_done = 1", ())

    def claim_for_resume(self, job_id: str) -> bool:
        """Atomic INTERRUPTED -> RUNNING transition. True only for the winner."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET state = ?, updated_at = ? "
                "WHERE job_id = ? AND state = ?",
                (
                    JobState.RUNNING.value,
                    datetime.now(UTC).isoformat(),
                    job_id,
                    JobState.INTERRUPTED.value,
                ),
            )
            self._conn.commit()
            return cur.rowcount == 1

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _update(self, job_id: str, set_clause: str, params: tuple) -> None:
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE jobs SET {set_clause}, updated_at = ? WHERE job_id = ?",
                (*params, datetime.now(UTC).isoformat(), job_id),
            )
            if cur.rowcount == 0:
                raise KeyError(job_id)
            self._conn.commit()

    def close(self) -> None:
        """Close the underlying connection (flushes WAL)."""
        with self._lock:
            self._conn.close()
