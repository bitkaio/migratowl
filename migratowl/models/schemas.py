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
    max_deps: int = Field(default=50, gt=0)
    ecosystems: list[Ecosystem] | None = None


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
