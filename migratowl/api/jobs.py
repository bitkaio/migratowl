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

"""Job store — pluggable persistence for async scan tracking.

``JobStore`` is a synchronous ``Protocol``. ``InMemoryJobStore`` is the default
(non-durable, used for CI/ephemeral); ``SqliteJobStore`` (see ``sqlite_jobs.py``)
adds durability for crash recovery. ``create_job_store`` selects the backend
from settings.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from uuid import uuid4

from migratowl.models.schemas import (
    JobState,
    JobStatus,
    ScanAnalysisReport,
    ScanWebhookPayload,
)

if TYPE_CHECKING:
    from migratowl.config import Settings


@runtime_checkable
class JobStore(Protocol):
    """Persistence contract for scan job lifecycle state."""

    def create(self, payload: ScanWebhookPayload) -> JobStatus: ...

    def get(self, job_id: str) -> JobStatus | None: ...

    def update_state(self, job_id: str, state: JobState) -> None: ...

    def set_result(self, job_id: str, result: ScanAnalysisReport) -> None: ...

    def set_error(self, job_id: str, error: str) -> None: ...

    def set_sandbox_id(self, job_id: str, sandbox_id: str | None) -> None: ...

    def increment_retry(self, job_id: str) -> int: ...

    def list_by_state(self, state: JobState) -> list[JobStatus]: ...

    def mark_side_effects_done(self, job_id: str) -> None: ...

    def claim_for_resume(self, job_id: str) -> bool: ...


class InMemoryJobStore:
    """Non-durable job store backed by a dict. Lost on process restart."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobStatus] = {}

    def create(self, payload: ScanWebhookPayload) -> JobStatus:
        """Create a new pending job and return its status."""
        job_id = str(uuid4())
        status = JobStatus(job_id=job_id, state=JobState.PENDING, payload=payload)
        self._jobs[job_id] = status
        return status

    def get(self, job_id: str) -> JobStatus | None:
        """Return job status or None if not found."""
        return self._jobs.get(job_id)

    def update_state(self, job_id: str, state: JobState) -> None:
        """Transition job to a new state."""
        job = self._require(job_id)
        job.state = state
        job.updated_at = datetime.now(UTC)

    def set_result(self, job_id: str, result: ScanAnalysisReport) -> None:
        """Mark job as completed with a result."""
        job = self._require(job_id)
        job.state = JobState.COMPLETED
        job.result = result
        job.updated_at = datetime.now(UTC)

    def set_error(self, job_id: str, error: str) -> None:
        """Mark job as failed with an error message."""
        job = self._require(job_id)
        job.state = JobState.FAILED
        job.error = error
        job.updated_at = datetime.now(UTC)

    def set_sandbox_id(self, job_id: str, sandbox_id: str | None) -> None:
        """Persist (or clear) the sandbox ID a job is bound to."""
        job = self._require(job_id)
        job.sandbox_id = sandbox_id
        job.updated_at = datetime.now(UTC)

    def increment_retry(self, job_id: str) -> int:
        """Increment and return the job's retry counter."""
        job = self._require(job_id)
        job.retry_count += 1
        job.updated_at = datetime.now(UTC)
        return job.retry_count

    def list_by_state(self, state: JobState) -> list[JobStatus]:
        """Return all jobs currently in the given state."""
        return [job for job in self._jobs.values() if job.state == state]

    def mark_side_effects_done(self, job_id: str) -> None:
        """Record that terminal side effects (PR comment, callback) have fired."""
        job = self._require(job_id)
        job.side_effects_done = True
        job.updated_at = datetime.now(UTC)

    def claim_for_resume(self, job_id: str) -> bool:
        """Atomically transition INTERRUPTED -> RUNNING.

        Returns True only for the caller that wins the transition; a job not in
        INTERRUPTED (or already claimed) yields False. Prevents two concurrent
        resume requests from running the same job twice.
        """
        job = self._jobs.get(job_id)
        if job is None or job.state != JobState.INTERRUPTED:
            return False
        job.state = JobState.RUNNING
        job.updated_at = datetime.now(UTC)
        return True

    def _require(self, job_id: str) -> JobStatus:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job


def create_job_store(settings: Settings) -> JobStore:
    """Return a JobStore for the configured persistence backend."""
    if settings.persistence_backend == "sqlite":
        from migratowl.api.sqlite_jobs import SqliteJobStore

        return SqliteJobStore(settings.jobs_db_path)
    return InMemoryJobStore()
