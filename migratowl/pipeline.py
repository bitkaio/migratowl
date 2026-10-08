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
import time
from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from migratowl.models.schemas import (
    AnalysisReport,
    Dependency,
    OutdatedDependency,
    RegistryFailure,
    ScanResult,
    ScanWebhookPayload,
)

if TYPE_CHECKING:
    from migratowl.agent.factory import MigratowlTools

logger = logging.getLogger(__name__)

_LEADING_OPERATORS = re.compile(r"^[\s^~>=<!v]+")
# Lazy name match anchored on the suffix: Java names are groupId:artifactId and contain ":".
_UPDATE_FAILURE = re.compile(r"^\s+(?P<name>.+?): FAILED \(exit -?\d+\)(?: — (?P<detail>.*))?$")


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


def dependency_is_major_bump(dep: OutdatedDependency) -> bool | None:
    """Major bump measured from the installed version (lockfile) when known, else the declared one."""
    return is_major_bump(dep.installed_version or dep.current_version, dep.latest_version)


def _major_gap(dep: OutdatedDependency) -> int:
    cur, new = parse_major(dep.installed_version or dep.current_version), parse_major(dep.latest_version)
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


async def prepare_scan(
    tools: MigratowlTools,
    payload: ScanWebhookPayload,
    config: RunnableConfig,
    *,
    tail_chars: int = 4000,
) -> PreparedScan:
    """Clone, scan, check outdated, update ``main/`` and validate — no LLM involved."""
    started = time.monotonic()

    clone_out = await tools.clone_repo.ainvoke(
        {"repo_url": payload.repo_url, "branch": payload.branch_name}, config=config
    )
    if clone_out.startswith("Failed"):
        raise PipelineError(clone_out)

    deps_raw = await tools.scan_dependencies.ainvoke({}, config=config)
    try:
        deps = [Dependency(**item) for item in json.loads(deps_raw)]
    except (ValueError, TypeError) as exc:
        raise PipelineError(f"Dependency scan failed: {deps_raw[:500]}") from exc
    if payload.ecosystems:
        deps = [dep for dep in deps if dep.ecosystem in payload.ecosystems]

    outdated: list[OutdatedDependency] = []
    failures: list[RegistryFailure] = []
    if deps:
        raw = await tools.check_outdated_deps.ainvoke(
            {"dependencies_json": json.dumps([dep.model_dump(mode="json") for dep in deps])}, config=config
        )
        data = json.loads(raw)
        outdated = [OutdatedDependency(**item) for item in data["outdated"]]
        failures = [RegistryFailure(**item) for item in data["failures"]]

    candidates, skipped = select_candidates(outdated, payload)
    prepared = PreparedScan(
        scan_result=ScanResult(
            all_deps=deps,
            outdated=outdated,
            manifests_found=sorted({dep.manifest_path for dep in deps}),
            scan_duration_seconds=round(time.monotonic() - started, 1),
            registry_failures=failures,
        ),
        candidates=candidates,
        skipped=skipped,
    )
    if not candidates:
        return prepared

    copy_out = await tools.copy_source.ainvoke({"folder_name": "main"}, config=config)
    if not copy_out.startswith("Successfully"):
        raise PipelineError(copy_out)

    by_ecosystem: dict[str, list[OutdatedDependency]] = {}
    for dep in candidates:
        by_ecosystem.setdefault(dep.ecosystem.value, []).append(dep)

    for ecosystem, packages in by_ecosystem.items():
        packages_json = json.dumps([
            {"name": p.name, "current_version": p.current_version, "latest_version": p.latest_version,
             "manifest_path": p.manifest_path}
            for p in packages
        ])
        summary = await tools.update_dependencies.ainvoke(
            {"folder_name": "main", "ecosystem": ecosystem, "packages_json": packages_json}, config=config
        )
        prepared.update_failures.update(parse_update_failures(summary))

    for ecosystem in by_ecosystem:
        raw = await tools.validate_project.ainvoke({"folder_name": "main", "ecosystem": ecosystem}, config=config)
        prepared.validations.append(summarize_validation(ecosystem, raw, tail_chars))

    logger.info(
        "Pipeline prepared %d candidate(s), %d skipped, %d update failure(s), validations=%s",
        len(candidates), len(skipped), len(prepared.update_failures),
        {v.ecosystem: v.passed for v in prepared.validations},
    )
    return prepared


def presolve(prepared: PreparedScan) -> tuple[list[AnalysisReport], list[OutdatedDependency]]:
    """Settle candidates that provably need no LLM; return ``(resolved, pending)``.

    Safe = its ecosystem validated green, its own update succeeded, and the bump is
    provably not major. Unknown versions are never assumed safe.
    """
    passed = {v.ecosystem: v.passed for v in prepared.validations}

    def is_safe(dep: OutdatedDependency) -> bool:
        return (
            passed.get(dep.ecosystem.value, False)
            and dep.name not in prepared.update_failures
            and dependency_is_major_bump(dep) is False
        )

    # One name can appear in several manifests; the report has one verdict per name, so
    # if any entry needs the LLM, every entry of that name does.
    unsafe_names = {dep.name.lower() for dep in prepared.candidates if not is_safe(dep)}
    resolved: list[AnalysisReport] = []
    pending: list[OutdatedDependency] = []
    seen_resolved: set[str] = set()
    for dep in prepared.candidates:
        key = dep.name.lower()
        if key in unsafe_names:
            pending.append(dep)
        elif key not in seen_resolved:
            seen_resolved.add(key)
            resolved.append(AnalysisReport(
                dependency_name=dep.name, is_breaking=False, error_summary="",
                changelog_citation="", suggested_human_fix="", confidence=1.0,
            ))
    return resolved, pending


def build_analysis_brief(
    payload: ScanWebhookPayload, prepared: PreparedScan, pending: list[OutdatedDependency]
) -> str:
    """Compact text the LLM analyses instead of driving Phases 1–2 itself."""
    lines = [
        f"Repository: {payload.repo_url} (branch {payload.branch_name}).",
        "Already done in code: the repo is cloned to source/, every package below was updated to its "
        "latest version in main/, and main/ was built and tested.",
        "",
        f"Packages to analyze ({len(pending)}):",
    ]
    for dep in pending:
        bump = dependency_is_major_bump(dep)
        version = dep.current_version
        if dep.installed_version:
            version = f"{dep.installed_version} (declared {dep.current_version})"
        kind = "MAJOR bump" if bump else ("minor/patch bump" if bump is False else "unknown bump size")
        lines.append(
            f"- {dep.name} {version} -> {dep.latest_version} "
            f"({dep.ecosystem.value}, {dep.manifest_path}, {kind})"
        )
    pending_names = {dep.name for dep in pending}
    failures = {name: detail for name, detail in prepared.update_failures.items() if name in pending_names}
    if failures:
        lines += ["", "Update failures (could not install the latest version):"]
        lines += [f"- {name}: {detail}" for name, detail in failures.items()]
    lines += ["", "Validation results:"]
    for v in prepared.validations:
        if v.passed:
            lines.append(f"- {v.ecosystem}: PASSED")
        else:
            lines.append(f'- {v.ecosystem}: FAILED at step "{v.failed_step}". Output tail:')
            lines.append(f"```\n{v.output_tail}\n```")
    lines += ["", "Return exactly one AnalysisReport per package listed above."]
    return "\n".join(lines)
