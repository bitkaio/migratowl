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

"""prepare_scan tool — runs the deterministic pipeline for agent-driven entrypoints (deep-agents-ui)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.tools import tool
from langchain_core.runnables.config import ensure_config
from pydantic import ValidationError

from migratowl.models.schemas import ScanWebhookPayload
from migratowl.pipeline import PipelineError, build_analysis_brief, prepare_scan, presolve

if TYPE_CHECKING:
    from migratowl.agent.factory import MigratowlTools


def create_prepare_scan_tool(tools: MigratowlTools, *, tail_chars: int) -> Any:
    """Create the prepare_scan tool bound to Migratowl's sandbox tools."""

    @tool("prepare_scan")
    async def prepare_scan_tool(repo_url: str, branch: str = "main", max_deps: int = 50) -> str:
        """Clone, scan, update and test the repository in one step, then return an analysis brief.

        Call this once, only when you were not already given a brief starting with "Repository:".

        Args:
            repo_url: HTTPS URL of the repository.
            branch: Branch to scan.
            max_deps: Maximum number of outdated dependencies to analyze.
        """
        try:
            payload = ScanWebhookPayload(repo_url=repo_url, branch_name=branch, max_deps=max_deps)
            prepared = await prepare_scan(tools, payload, ensure_config(), tail_chars=tail_chars)
        except (PipelineError, ValidationError) as exc:
            # Return to the agent so it can tell the user; raising would abort the whole run.
            return f"prepare_scan failed: {exc}"
        resolved, pending = presolve(prepared)
        if not pending:
            safe = ", ".join(r.dependency_name for r in resolved) or "none"
            return f"Repository: {repo_url}. Nothing to analyze: no outdated package needs review (safe: {safe})."
        return build_analysis_brief(payload, prepared, pending)

    return prepare_scan_tool
