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
    current_version: str  # as declared in the manifest (may be a range)
    ecosystem: Ecosystem
    manifest_path: str
    installed_version: str | None = None  # from a lockfile, when one exists
    # Where the version is defined when not on the dependency line:
    # a pom.xml <properties> name or a Gradle catalog [versions] key.
    version_key: str | None = None


class OutdatedDependency(BaseModel):
    """Dependency with available update information."""

    name: str
    current_version: str
    latest_version: str
    ecosystem: Ecosystem
    manifest_path: str
    installed_version: str | None = None
    # Go only: the new module path when the latest version is a new major
    # (github.com/x/y → github.com/x/y/v2); None when the path does not change.
    module_path: str | None = None
    version_key: str | None = None  # see Dependency.version_key
    homepage_url: str | None = None
    repository_url: str | None = None
    changelog_url: str | None = None


class RegistryFailure(BaseModel):
    """A dependency whose registry query could not be completed."""

    name: str
    ecosystem: Ecosystem


class ScanResult(BaseModel):
    """Phase 0 output: all dependencies, outdated ones, and scan metadata."""

    all_deps: list[Dependency]
    outdated: list[OutdatedDependency]
    manifests_found: list[str]
    scan_duration_seconds: float
    registry_failures: list[RegistryFailure] = []


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


class PackageVerdicts(BaseModel):
    """LLM output of the analysis step: one report per package it was asked about."""

    reports: list[AnalysisReport]


class EvidenceHit(BaseModel):
    """Where a breaking-change rule matched the code (found by static analysis, not by the model)."""

    rule: str
    note: str = ""
    file: str
    line: int
    text: str = ""


class PackageEvidence(BaseModel):
    """How the repository uses a package, from parsing ``source/`` with ast-grep in the sandbox."""

    importing_files: list[str] = []
    importing_count: int = 0
    test_files: list[str] = []
    tests_reach: bool | None = None  # None = could not tell
    hits: list[EvidenceHit] = []


class ScanAnalysisReport(BaseModel):
    """Top-level pipeline output combining scan and analysis results."""

    repo_url: str
    branch_name: str
    scan_result: ScanResult
    reports: list[AnalysisReport]
    skipped: list[str] = []
    total_duration_seconds: float
    # total_input_tokens includes cache reads and writes (also counted below)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    model_name: str = ""
    # Static-analysis evidence per analyzed package, and the "safe" verdicts it contradicts
    # (package → reason). Computed in code, never by the model.
    evidence: dict[str, PackageEvidence] = {}
    reviews: dict[str, str] = {}


class JobState(enum.StrEnum):
    """Lifecycle states for an async scan job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class JobStatus(BaseModel):
    """Status record for an async scan job."""

    job_id: str
    state: JobState
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: ScanWebhookPayload
    result: ScanAnalysisReport | None = None
    error: str | None = None
    # Crash recovery
    sandbox_id: str | None = None
    retry_count: int = 0
    # Guards duplicate PR comments / callbacks when a job is resumed.
    side_effects_done: bool = False


class WebhookAcceptedResponse(BaseModel):
    """202 response returned when a scan is accepted."""

    job_id: str
    status_url: str