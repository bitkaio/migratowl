"""Helpers for converting webhook payloads to agent inputs and outputs."""

from __future__ import annotations

import json
import logging

from langchain_core.messages import BaseMessage

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


def _message_text(msg: object) -> str:
    """Extract plain-text content from a message (dict or LangChain BaseMessage)."""
    if isinstance(msg, BaseMessage):
        content = msg.content
        return content if isinstance(content, str) else ""
    if isinstance(msg, dict):
        content = msg.get("content", "")
        return content if isinstance(content, str) else ""
    return ""


def extract_report(agent_result: dict, payload: ScanWebhookPayload) -> ScanAnalysisReport:
    """Extract a ScanAnalysisReport from the agent result.

    Strategy:
      1. Read ``structured_response`` (set by deepagents when response_format is used).
      2. Fallback: scan messages for JSON-parseable content (covers providers where
         ToolStrategy fails to populate structured_response).
      3. Return an empty report if nothing works.
    """
    # 1. Primary: structured_response key (ProviderStrategy / Anthropic)
    structured = agent_result.get("structured_response")
    if structured is not None:
        try:
            if isinstance(structured, ScanAnalysisReport):
                return structured
            return ScanAnalysisReport.model_validate(structured)
        except Exception:
            logger.debug("structured_response present but failed validation")

    # 2. Fallback: parse JSON from messages (ToolStrategy / OpenAI fallback)
    for msg in reversed(agent_result.get("messages", [])):
        content = _message_text(msg)
        if not content:
            continue
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
