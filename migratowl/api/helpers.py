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

"""Helpers for converting webhook payloads to agent inputs and outputs."""

from __future__ import annotations

import json
import logging

from langchain_core.messages import AIMessage, BaseMessage

from migratowl.models.schemas import (
    OutdatedDependency,
    ScanAnalysisReport,
    ScanWebhookPayload,
)

logger = logging.getLogger(__name__)


def _accumulate_tokens(messages: list[object]) -> tuple[int, int]:
    """Sum input and output token counts from AIMessage.usage_metadata."""
    input_tokens = 0
    output_tokens = 0
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        meta = msg.usage_metadata
        if meta is None:
            continue
        input_tokens += meta["input_tokens"]
        output_tokens += meta["output_tokens"]
    return input_tokens, output_tokens


def build_user_message(payload: ScanWebhookPayload) -> str:
    """Convert a webhook payload into a natural-language instruction for the agent."""
    parts = [
        f"Scan the repository at {payload.repo_url} on branch {payload.branch_name}.",
        f"Analyze up to {payload.max_deps} dependencies.",
    ]
    if payload.exclude_deps:
        parts.append(f"Exclude these dependencies: {', '.join(payload.exclude_deps)}.")
    if payload.check_deps:
        parts.append(f"Only check these dependencies: {', '.join(payload.check_deps)}.")
    if payload.ecosystems:
        eco_names = ", ".join(e.value for e in payload.ecosystems)
        parts.append(f"Only scan these ecosystems: {eco_names}.")
    return " ".join(parts)


def _message_text(msg: object) -> str:
    """Extract plain-text content from a message (dict or LangChain BaseMessage)."""
    if isinstance(msg, BaseMessage):
        content = msg.content
        return content if isinstance(content, str) else ""
    if isinstance(msg, dict):
        content = msg.get("content", "")
        return content if isinstance(content, str) else ""
    return ""


class ReportExtractionError(Exception):
    """The agent finished without returning a usable ScanAnalysisReport."""


def extract_report(agent_result: dict, payload: ScanWebhookPayload) -> ScanAnalysisReport:
    """Extract a ScanAnalysisReport from the agent result.

    Strategy:
      1. Read ``structured_response`` (set by deepagents when response_format is used).
      2. Fallback: scan messages for JSON-parseable content (covers providers where
         ToolStrategy fails to populate structured_response).
      3. Raise ``ReportExtractionError`` if nothing works — an empty report would be
         indistinguishable from "nothing is outdated".
    """
    report: ScanAnalysisReport | None = None

    # 1. Primary: structured_response key (ProviderStrategy / Anthropic)
    structured = agent_result.get("structured_response")
    if structured is not None:
        try:
            if isinstance(structured, ScanAnalysisReport):
                report = structured
            else:
                report = ScanAnalysisReport.model_validate(structured)
        except Exception:
            logger.debug("structured_response present but failed validation")

    # 2. Fallback: parse JSON from messages (ToolStrategy / OpenAI fallback)
    if report is None:
        for msg in reversed(agent_result.get("messages", [])):
            content = _message_text(msg)
            if not content:
                continue
            try:
                data = json.loads(content)
                report = ScanAnalysisReport.model_validate(data)
                break
            except (json.JSONDecodeError, Exception):
                continue

    # 3. No report: fail loudly instead of reporting "nothing outdated"
    if report is None:
        raise ReportExtractionError("Agent finished without returning a structured report")

    # Accumulate token usage from all messages
    messages = agent_result.get("messages", [])
    report.total_input_tokens, report.total_output_tokens = _accumulate_tokens(messages)

    # Fix the skipped list: only include outdated deps that weren't analyzed
    analyzed_names = [r.dependency_name for r in report.reports]
    report.skipped = compute_skipped(
        report.scan_result.outdated, analyzed_names, report.skipped
    )

    return report


def compute_skipped(
    outdated: list[OutdatedDependency],
    analyzed_names: list[str],
    agent_skipped: list[str],  # noqa: ARG001 — kept for API compatibility, ignored
) -> list[str]:
    """Compute the correct skipped list from outdated deps and analyzed reports.

    The skipped list should only contain outdated dependencies that were not
    analyzed (due to max_deps limit or other reasons). Non-outdated deps should
    never appear in skipped — they simply weren't candidates for analysis.

    Args:
        outdated: List of outdated dependencies from the registry check.
        analyzed_names: Names of dependencies that were actually analyzed.
        agent_skipped: The agent's skipped list (ignored — we compute it correctly).

    Returns:
        List of outdated dependency names that were not analyzed.
    """
    outdated_names = {dep.name for dep in outdated}
    analyzed_set = set(analyzed_names)
    return [name for name in outdated_names if name not in analyzed_set]