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

"""Deterministic scan preparation: Phases 1–2 run in code, not by the LLM.

Clone, scan, outdated check, update and validation follow a fixed order and need no
judgement. Running them here keeps small models from wandering and keeps their context
small; the LLM only sees a compact brief of the result.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel

from migratowl.models.schemas import OutdatedDependency, ScanResult, ScanWebhookPayload

logger = logging.getLogger(__name__)

_LEADING_OPERATORS = re.compile(r"^[\s^~>=<!v]+")
_UPDATE_FAILURE = re.compile(r"^\s+(?P<name>[^:]+): FAILED \(exit -?\d+\)(?: — (?P<detail>.*))?$")


class PipelineError(Exception):
    """Phases 1–2 could not complete (e.g. clone failed); the scan cannot proceed."""


class EcosystemValidation(BaseModel):
    """Pass/fail of the combined update for one ecosystem, with the failing output tail."""

    ecosystem: str
    passed: bool
    failed_step: str | None = None
    output_tail: str = ""


class PreparedScan(BaseModel):
    """Everything Phases 1–2 produce, ready for presolve and the LLM brief."""

    scan_result: ScanResult
    candidates: list[OutdatedDependency]
    skipped: list[str]
    update_failures: dict[str, str] = {}
    validations: list[EcosystemValidation] = []


def parse_major(version: str) -> int | None:
    """Major version from a version or constraint string (``>=3.2.10`` → 3), or None."""
    match = re.match(r"(\d+)", _LEADING_OPERATORS.sub("", version or ""))
    return int(match.group(1)) if match else None


def is_major_bump(current: str, latest: str) -> bool | None:
    """True/False when both majors parse; None when unknown (never treat unknown as safe)."""
    cur, new = parse_major(current), parse_major(latest)
    if cur is None or new is None:
        return None
    return new > cur


def _major_gap(dep: OutdatedDependency) -> int:
    cur, new = parse_major(dep.current_version), parse_major(dep.latest_version)
    return new - cur if cur is not None and new is not None else 0


def select_candidates(
    outdated: list[OutdatedDependency], payload: ScanWebhookPayload
) -> tuple[list[OutdatedDependency], list[str]]:
    """Apply exclude/check filters, rank by major gap, cap at ``max_deps``.

    Returns ``(candidates, skipped)``; ``skipped`` holds the names cut by ``max_deps``.
    """
    exclude = {name.lower() for name in payload.exclude_deps}
    only = {name.lower() for name in payload.check_deps}
    pool = [
        dep
        for dep in outdated
        if dep.name.lower() not in exclude and (not only or dep.name.lower() in only)
    ]
    pool.sort(key=lambda dep: (-_major_gap(dep), dep.name.lower()))
    return pool[: payload.max_deps], [dep.name for dep in pool[payload.max_deps :]]


def parse_update_failures(summary: str) -> dict[str, str]:
    """Map package name → failure detail from ``update_dependencies``' summary text."""
    failures: dict[str, str] = {}
    for line in summary.splitlines():
        match = _UPDATE_FAILURE.match(line)
        if match and not match["name"].startswith("("):
            failures[match["name"].strip()] = (match["detail"] or "").strip()
    return failures


def summarize_validation(ecosystem: str, raw: str, tail_chars: int) -> EcosystemValidation:
    """Reduce ``validate_project`` JSON to pass/fail plus the tail of the failing step."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return EcosystemValidation(
            ecosystem=ecosystem, passed=False, failed_step="validate", output_tail=raw[-tail_chars:]
        )
    if "error" in data:
        return EcosystemValidation(
            ecosystem=ecosystem, passed=False, failed_step="validate", output_tail=str(data["error"])[-tail_chars:]
        )
    failed = next((s for s in data.get("steps", []) if s.get("exit_code", 0) != 0), None)
    if data.get("passed") and failed is None:
        return EcosystemValidation(ecosystem=ecosystem, passed=True)
    failed = failed or {}
    return EcosystemValidation(
        ecosystem=ecosystem,
        passed=False,
        failed_step=failed.get("name"),
        output_tail=str(failed.get("output", ""))[-tail_chars:],
    )
