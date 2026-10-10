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
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from migratowl.models.schemas import (
    AnalysisReport,
    PackageEvidence,
    PackageVerdicts,
    ScanAnalysisReport,
    ScanWebhookPayload,
)

if TYPE_CHECKING:
    from migratowl.pipeline import PreparedScan

logger = logging.getLogger(__name__)


class TokenUsage(BaseModel):
    """Token totals for a scan; ``input`` includes cache reads and writes."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_creation: int = 0


def sum_usage(usages: Iterable[Mapping[str, Any]]) -> TokenUsage:
    """Sum LangChain ``UsageMetadata`` dicts (e.g. a ``UsageMetadataCallbackHandler``'s values)."""
    total = TokenUsage()
    for usage in usages:
        details = usage.get("input_token_details") or {}
        total.input += usage.get("input_tokens") or 0
        total.output += usage.get("output_tokens") or 0
        total.cache_read += details.get("cache_read") or 0
        total.cache_creation += details.get("cache_creation") or 0
    return total


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
    messages = agent_result.get("messages", [])
    for msg in reversed(messages):
        content = _message_text(msg)
        if not content:
            continue
        try:
            return PackageVerdicts.model_validate(json.loads(content)).reports
        except Exception:
            continue
    if any(_message_text(m).startswith(_CALL_LIMIT_PREFIX) for m in messages):
        # The model-call cap ended the run: keep what the package-analyzer subagents already
        # returned; every other package is reported as skipped (never as safe).
        finished = _subagent_verdicts(messages)
        logger.warning("Model-call limit reached; keeping %d finished verdict(s)", len(finished))
        return finished
    raise ReportExtractionError("Agent finished without returning a structured report")


# What langchain's ModelCallLimitMiddleware leaves as the last message when it ends a run.
_CALL_LIMIT_PREFIX = "Model call limits exceeded"


def call_limit_reached(agent_result: dict) -> bool:
    """Whether the model-call cap ended the analysis agent's run (a subagent's cap reaches it as a tool result)."""
    return any(
        getattr(m, "type", "") == "ai" and _message_text(m).startswith(_CALL_LIMIT_PREFIX)
        for m in agent_result.get("messages", [])
    )


def _subagent_verdicts(messages: list[Any]) -> list[AnalysisReport]:
    """Single-package ``AnalysisReport`` JSON objects returned by subagents (tool messages)."""
    reports: list[AnalysisReport] = []
    for msg in messages:
        if getattr(msg, "type", "") != "tool":
            continue
        try:
            reports.append(AnalysisReport.model_validate(json.loads(_message_text(msg))))
        except Exception:
            continue
    return reports


def _reviews(
    prepared: PreparedScan, verdicts: dict[str, AnalysisReport], evidence: dict[str, PackageEvidence]
) -> dict[str, str]:
    """'Safe' verdicts the static evidence contradicts, with the reason (package → reason).

    Flagged when a breaking-change rule matched the code, or when a major bump was judged safe with
    no changelog evidence on tests that never reach the package. Missing evidence flags nothing.
    """
    from migratowl.pipeline import dependency_is_major_bump  # pipeline imports this module's callers

    deps = {dep.name: dep for dep in prepared.candidates}
    reviews: dict[str, str] = {}
    for name, verdict in verdicts.items():
        ev = evidence.get(name)
        if verdict.is_breaking or ev is None:
            continue
        if ev.hits:
            first = ev.hits[0]
            more = f" (+{len(ev.hits) - 1} more)" if len(ev.hits) > 1 else ""
            reviews[name] = f"{first.note} — found at {first.file}:{first.line}{more}"
            continue
        dep = deps.get(name)
        has_changelog = bool(prepared.changelog_excerpts.get(name) or verdict.changelog_citation.strip())
        if dep is not None and dependency_is_major_bump(dep) and ev.tests_reach is False and not has_changelog:
            reviews[name] = (
                "Major upgrade judged safe on passing tests, but no test reaches this package and no "
                "changelog evidence was found"
            )
    return reviews


def assemble_report(
    payload: ScanWebhookPayload,
    prepared: PreparedScan,
    reports: list[AnalysisReport],
    *,
    duration: float,
    tokens: TokenUsage,
) -> ScanAnalysisReport:
    """Build the final report in code; the LLM only contributes per-package verdicts.

    Verdict names are matched case-insensitively to candidates and rewritten to the
    canonical name; unknown names are dropped, and candidates without a verdict go
    to ``skipped`` so they are never reported as safe by omission.
    """
    canonical: dict[str, str] = {}
    for dep in prepared.candidates:
        canonical.setdefault(dep.name.lower(), dep.name)
    by_name: dict[str, AnalysisReport] = {}
    for report in reports:
        name = canonical.get(report.dependency_name.lower())
        if name is None:
            continue
        current = by_name.get(name)
        # Several verdicts for one name (same package in several manifests): breaking wins.
        if current is None or (report.is_breaking and not current.is_breaking):
            update: dict[str, str] = {"dependency_name": name}
            # Back an empty citation with the changelog excerpt fetched in code.
            excerpt = prepared.changelog_excerpts.get(name)
            if excerpt and not report.changelog_citation.strip():
                update["changelog_citation"] = excerpt
            by_name[name] = report.model_copy(update=update)
    names = list(canonical.values())
    evidence = {name: ev for name, ev in prepared.evidence.items() if name in by_name}
    reviews = _reviews(prepared, by_name, evidence)
    missing = [name for name in names if name not in by_name]
    if missing:
        logger.warning("No verdict for %s; reporting them as skipped", ", ".join(missing))
    return ScanAnalysisReport(
        repo_url=payload.repo_url,
        branch_name=payload.branch_name,
        scan_result=prepared.scan_result,
        reports=[by_name[name] for name in names if name in by_name],
        skipped=prepared.skipped + missing,
        total_duration_seconds=round(duration, 1),
        total_input_tokens=tokens.input,
        total_output_tokens=tokens.output,
        total_cache_read_tokens=tokens.cache_read,
        total_cache_creation_tokens=tokens.cache_creation,
        evidence=evidence,
        reviews=reviews,
    )
