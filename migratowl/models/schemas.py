"""Webhook and API schemas for MigratOwl."""

import enum
from typing import TypedDict

from pydantic import BaseModel, Field


class Ecosystem(enum.StrEnum):
    """Supported language ecosystems."""

    PYTHON = "python"
    NODEJS = "nodejs"
    GO = "go"
    RUST = "rust"


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
    git_provider: str = "github"
    pr_number: int | None = None
    callback_url: str | None = None
    exclude_deps: list[str] = []
    max_deps: int = Field(default=50, gt=0)


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
