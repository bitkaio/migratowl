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

"""Resume logic: reconnect to a surviving sandbox, or restart from scratch.

This module isolates the coupling to langchain-kubernetes private API
(``manager._provider.areconnect`` / ``manager._sandbox_by_thread``) so a future
migration to the LangGraph-native sandbox integration touches one file.

Reconnect-or-restart semantics:
- Pod survived and is live → seed the manager's per-thread cache so the agent
  reuses it, and resume from the checkpoint.
- Pod gone (or reconnect returned an optimistic-but-dead backend) → delete the
  checkpoint thread so the agent re-runs from scratch against a fresh sandbox.
  Resuming stale reasoning against an empty workspace would produce wrong results.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from langchain_kubernetes import SandboxNotFoundError

if TYPE_CHECKING:
    from migratowl.api.jobs import JobStore
    from migratowl.models.schemas import JobStatus

logger = logging.getLogger(__name__)

Outcome = Literal["reconnected", "restarted"]


async def _is_live(sandbox: Any) -> bool:
    """Probe a reconnected sandbox with a trivial command.

    agent-sandbox reconnect can be optimistic (returns a backend without
    verifying the claim), so a successful probe — not merely the absence of
    ``SandboxNotFoundError`` — is what confirms the pod is usable.
    """
    try:
        await sandbox.aexecute("true")
        return True
    except Exception:
        return False


async def _delete_abandoned(manager: Any, sandbox_id: str) -> None:
    """Best-effort delete of a sandbox the restart will not reuse."""
    try:
        await manager._provider.adelete(sandbox_id=sandbox_id)
    except Exception:
        logger.warning("Resume: could not delete abandoned sandbox %s", sandbox_id, exc_info=True)


async def reconnect_or_restart(
    manager: Any,
    job: JobStatus,
    store: JobStore,
    checkpointer: Any,
) -> Outcome:
    """Reconnect to the job's surviving sandbox, or clear its checkpoint to restart.

    Returns ``"reconnected"`` when the live pod was reattached (agent resumes from
    checkpoint) or ``"restarted"`` when the checkpoint was cleared for a fresh run.
    """
    if job.sandbox_id:
        try:
            sandbox = await manager._provider.areconnect(job.sandbox_id)
            if await _is_live(sandbox):
                manager._sandbox_by_thread[job.job_id] = sandbox
                logger.info("Resume: reconnected job %s to sandbox %s", job.job_id, job.sandbox_id)
                return "reconnected"
            logger.info(
                "Resume: sandbox %s reconnected but not live — restarting job %s",
                job.sandbox_id,
                job.job_id,
            )
            # The restart provisions a fresh sandbox; delete this one instead of
            # leaving it running until the idle-TTL sweep.
            await _delete_abandoned(manager, job.sandbox_id)
        except SandboxNotFoundError:
            logger.info(
                "Resume: sandbox %s gone — restarting job %s from scratch",
                job.sandbox_id,
                job.job_id,
            )

    # Restart: clear the checkpoint so the agent re-runs Phase 1 against a fresh
    # sandbox instead of resuming reasoning that assumes a populated workspace.
    await checkpointer.adelete_thread(job.job_id)
    store.set_sandbox_id(job.job_id, None)
    return "restarted"
