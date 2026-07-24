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

"""Startup reconciliation of jobs orphaned by a crash.

Assumes a SINGLE app process (see the deployment notes). On boot, any job left
in ``RUNNING`` or ``PENDING`` was orphaned when the previous process died — no
scan task can still be alive — so it is transitioned to ``INTERRUPTED`` (eligible
for manual resume), unless it has already exhausted its retry budget, in which
case it is marked ``FAILED``.

``PENDING`` jobs never started (they were queued behind the scan semaphore), so
resuming them is a clean fresh run.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from migratowl.models.schemas import JobState

if TYPE_CHECKING:
    from migratowl.api.jobs import JobStore
    from migratowl.config import Settings

logger = logging.getLogger(__name__)

# States that indicate an in-flight job orphaned by a crash.
_ORPHAN_STATES = (JobState.RUNNING, JobState.PENDING)


def reconcile_orphans(store: JobStore, settings: Settings) -> None:
    """Transition crash-orphaned jobs to INTERRUPTED (or FAILED past the cap)."""
    for state in _ORPHAN_STATES:
        for job in store.list_by_state(state):
            if job.retry_count >= settings.max_scan_retries:
                store.set_error(
                    job.job_id, "Scan interrupted; exceeded max retries"
                )
                logger.warning(
                    "Reconcile: job %s exceeded max retries (%d) — marked FAILED",
                    job.job_id,
                    job.retry_count,
                )
            else:
                store.update_state(job.job_id, JobState.INTERRUPTED)
                logger.info(
                    "Reconcile: job %s (%s) orphaned by crash — marked INTERRUPTED",
                    job.job_id,
                    state.value,
                )
