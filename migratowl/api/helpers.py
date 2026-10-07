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
from typing import TYPE_CHECKING

from langchain_core.messages import AIMessage, BaseMessage

from migratowl.models.schemas import (
    AnalysisReport,
    PackageVerdicts,
    ScanAnalysisReport,
    ScanWebhookPayload,
)

if TYPE_CHECKING:
    from migratowl.pipeline import PreparedScan

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


def extract_verdicts(agent_result: dict) -> list[AnalysisReport]:
    """Read ``PackageVerdicts`` from ``structured_response``, else from JSON in messages.

    Raises ``ReportExtractionError`` when neither yields verdicts.
    """
    structured = agent_result.get("structured_response")
    if structured is not None:
        try:
            verdicts = (
                structured
                if isinstance(structured, PackageVerdicts)
                else PackageVerdicts.model_validate(structured)
            )
            return verdicts.reports
        except Exception:
            logger.debug("structured_response present but failed validation")
    for msg in reversed(agent_result.get("messages", [])):
        content = _message_text(msg)
        if not content:
            continue
        try:
            return PackageVerdicts.model_validate(json.loads(content)).reports
        except Exception:
            continue
    raise ReportExtractionError("Agent finished without returning a structured report")


def assemble_report(
    payload: ScanWebhookPayload,
    prepared: PreparedScan,
    reports: list[AnalysisReport],
    *,
    duration: float,
    tokens: tuple[int, int],
) -> ScanAnalysisReport:
    """Build the final report in code; the LLM only contributes per-package verdicts.

    Verdict names are matched case-insensitively to candidates and rewritten to the
    canonical name; unknown names are dropped, and candidates without a verdict go
    to ``skipped`` so they are never reported as safe by omission.
    """
    canonical = {dep.name.lower(): dep.name for dep in prepared.candidates}
    by_name: dict[str, AnalysisReport] = {}
    for report in reports:
        name = canonical.get(report.dependency_name.lower())
        if name is not None and name not in by_name:
            by_name[name] = report.model_copy(update={"dependency_name": name})
    missing = [dep.name for dep in prepared.candidates if dep.name not in by_name]
    if missing:
        logger.warning("No verdict for %s; reporting them as skipped", ", ".join(missing))
    return ScanAnalysisReport(
        repo_url=payload.repo_url,
        branch_name=payload.branch_name,
        scan_result=prepared.scan_result,
        reports=[by_name[dep.name] for dep in prepared.candidates if dep.name in by_name],
        skipped=prepared.skipped + missing,
        total_duration_seconds=round(duration, 1),
        total_input_tokens=tokens[0],
        total_output_tokens=tokens[1],
    )
