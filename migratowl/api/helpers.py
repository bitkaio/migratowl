"""Helpers for converting webhook payloads to agent inputs and outputs."""

from __future__ import annotations

import json
import logging

from migratowl.models.schemas import ScanAnalysisReport, ScanResult, ScanWebhookPayload

logger = logging.getLogger(__name__)


def build_user_message(payload: ScanWebhookPayload) -> str:
    """Convert a webhook payload into a natural-language instruction for the agent."""
    parts = [
        f"Scan the repository at {payload.repo_url} on branch {payload.branch_name}.",
        f"Analyze up to {payload.max_deps} dependencies.",
    ]
    if payload.exclude_deps:
        parts.append(f"Exclude these dependencies: {', '.join(payload.exclude_deps)}.")
    if payload.ecosystems:
        eco_names = ", ".join(e.value for e in payload.ecosystems)
        parts.append(f"Only scan these ecosystems: {eco_names}.")
    return " ".join(parts)


def extract_report(agent_result: dict, payload: ScanWebhookPayload) -> ScanAnalysisReport:
    """Parse the agent's final message into a ScanAnalysisReport.

    Falls back to an empty report if parsing fails.
    """
    messages = agent_result.get("messages", [])

    # Try parsing from the last assistant message backwards
    for msg in reversed(messages):
        content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
        try:
            data = json.loads(content)
            return ScanAnalysisReport.model_validate(data)
        except (json.JSONDecodeError, Exception):
            continue

    logger.warning("Could not extract ScanAnalysisReport from agent output; returning empty report")
    return ScanAnalysisReport(
        repo_url=payload.repo_url,
        branch_name=payload.branch_name,
        scan_result=ScanResult(
            all_deps=[], outdated=[], manifests_found=[], scan_duration_seconds=0.0
        ),
        reports=[],
        total_duration_seconds=0.0,
    )
