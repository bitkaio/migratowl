"""In-memory job store for async scan tracking."""

from datetime import UTC, datetime
from uuid import uuid4

from migratowl.models.schemas import (
    JobState,
    JobStatus,
    ScanAnalysisReport,
    ScanWebhookPayload,
)


class JobStore:
    """Thread-safe in-memory store for scan job status."""

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
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        job.state = state
        job.updated_at = datetime.now(UTC)

    def set_result(self, job_id: str, result: ScanAnalysisReport) -> None:
        """Mark job as completed with a result."""
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        job.state = JobState.COMPLETED
        job.result = result
        job.updated_at = datetime.now(UTC)

    def set_error(self, job_id: str, error: str) -> None:
        """Mark job as failed with an error message."""
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        job.state = JobState.FAILED
        job.error = error
        job.updated_at = datetime.now(UTC)
