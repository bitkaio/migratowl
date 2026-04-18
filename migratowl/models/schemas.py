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

"""Webhook and API schemas for Migratowl."""

import enum
from datetime import UTC, datetime
from typing import Literal, TypedDict

from pydantic import BaseModel, Field


class Ecosystem(enum.StrEnum):
    """Supported language ecosystems."""

    PYTHON = "python"
    NODEJS = "nodejs"
    GO = "go"
    RUST = "rust"
    JAVA = "java"


class OutdatedCheckMode(enum.StrEnum):
    """Controls how the latest available version is resolved.

    SAFE   — respect the declared semver constraint; only flag if a newer
             version exists *within* the declared range (e.g. ^4.21.2 → look
             for newer 4.x only).
    NORMAL — ignore the constraint entirely; compare the bare version against
             the globally highest published version (e.g. ^4.21.2 → compare
             4.21.2 against 5.x if it exists).
    """

    SAFE = "safe"
    NORMAL = "normal"


class LanguageDetection(BaseModel):
    """Detected language ecosystem in a repository."""

    ecosystem: Ecosystem
    marker_file: str
    project_root: str
    default_test_command: str
    default_install_command: str


class ScanWebhookPayload(BaseModel):
    """Payload received to trigger a repository scan."""

    repo_url: str
    branch_name: str = "main"
    git_provider: Literal["github", "gitlab"] = "github"
    pr_number: int | None = None
    commit_sha: str | None = None
    callback_url: str | None = None
    exclude_deps: list[str] = []
    check_deps: list[str] = []
    max_deps: int = Field(default=50, gt=0)
    ecosystems: list[Ecosystem] | None = None
    mode: OutdatedCheckMode = OutdatedCheckMode.NORMAL
    include_prerelease: bool = False


class Dependency(BaseModel):
    """Single dependency from manifest scanning."""

    name: str
    current_version: str
    ecosystem: Ecosystem
    manifest_path: str


class OutdatedDependency(BaseModel):
    """Dependency with available update information."""

    name: str
    current_version: str
    latest_version: str
    ecosystem: Ecosystem
    manifest_path: str
    homepage_url: str | None = None
    repository_url: str | None = None
    changelog_url: str | None = None


class ScanResult(BaseModel):
    """Phase 0 output: all dependencies, outdated ones, and scan metadata."""

    all_deps: list[Dependency]
    outdated: list[OutdatedDependency]
    manifests_found: list[str]
    scan_duration_seconds: float


class ExecutionResult(BaseModel):
    """Sandbox command execution result."""

    command_run: str
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False


class ChangelogResult(TypedDict):
    """Return envelope for fetch_changelog tool."""

    content: str
    source: str
    strategy_used: int
    truncated: bool
    format_warning: bool


class PackageConfidence(BaseModel):
    """Per-package confidence that this package caused a failure."""

    name: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class MainExecutionAnalysis(BaseModel):
    """Agent's analysis after running main/ with all deps updated."""

    packages_likely_breaking: list[PackageConfidence]
    packages_likely_safe: list[str]
    overall_test_passed: bool
    raw_error_summary: str


class AnalysisReport(BaseModel):
    """Per-dependency agent analysis output."""

    dependency_name: str
    is_breaking: bool
    error_summary: str
    changelog_citation: str
    suggested_human_fix: str
    confidence: float = Field(ge=0.0, le=1.0)


class ScanAnalysisReport(BaseModel):
    """Top-level pipeline output combining scan and analysis results."""

    repo_url: str
    branch_name: str
    scan_result: ScanResult
    reports: list[AnalysisReport]
    skipped: list[str] = []
    total_duration_seconds: float


class JobState(enum.StrEnum):
    """Lifecycle states for an async scan job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatus(BaseModel):
    """Status record for an async scan job."""

    job_id: str
    state: JobState
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: ScanWebhookPayload
    result: ScanAnalysisReport | None = None
    error: str | None = None


class WebhookAcceptedResponse(BaseModel):
    """202 response returned when a scan is accepted."""

    job_id: str
    status_url: str