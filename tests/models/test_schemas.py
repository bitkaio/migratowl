"""Tests for pipeline data models and schemas."""

import pytest
from pydantic import ValidationError

from migratowl.models.schemas import (
    AnalysisReport,
    ChangelogResult,
    Dependency,
    Ecosystem,
    ExecutionResult,
    JobState,
    JobStatus,
    MainExecutionAnalysis,
    OutdatedCheckMode,
    OutdatedDependency,
    PackageConfidence,
    ScanAnalysisReport,
    ScanResult,
    ScanWebhookPayload,
    WebhookAcceptedResponse,
)


class TestEcosystem:
    def test_valid_values(self) -> None:
        assert Ecosystem("python") == Ecosystem.PYTHON
        assert Ecosystem("nodejs") == Ecosystem.NODEJS
        assert Ecosystem("go") == Ecosystem.GO
        assert Ecosystem("rust") == Ecosystem.RUST
        assert Ecosystem("java") == Ecosystem.JAVA

    def test_invalid_value(self) -> None:
        with pytest.raises(ValueError):
            Ecosystem("kotlin")

    def test_serializes_as_string(self) -> None:
        assert Ecosystem.PYTHON.value == "python"


class TestScanWebhookPayload:
    def test_valid_payload_all_fields(self) -> None:
        payload = ScanWebhookPayload(
            repo_url="https://github.com/psf/requests",
            branch_name="develop",
            callback_url="https://example.com/callback",
            git_provider="gitlab",
            pr_number=42,
            ecosystems=[Ecosystem.PYTHON],
            exclude_deps=["setuptools"],
            check_deps=["requests"],
            max_deps=10,
            commit_sha="deadbeef1234",
        )
        assert payload.repo_url == "https://github.com/psf/requests"
        assert payload.branch_name == "develop"
        assert payload.callback_url == "https://example.com/callback"
        assert payload.git_provider == "gitlab"
        assert payload.pr_number == 42
        assert payload.ecosystems == [Ecosystem.PYTHON]
        assert payload.exclude_deps == ["setuptools"]
        assert payload.check_deps == ["requests"]
        assert payload.max_deps == 10
        assert payload.commit_sha == "deadbeef1234"

    def test_defaults(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/psf/requests")
        assert payload.branch_name == "main"
        assert payload.callback_url is None
        assert payload.git_provider == "github"
        assert payload.pr_number is None
        assert payload.ecosystems is None
        assert payload.exclude_deps == []
        assert payload.check_deps == []
        assert payload.max_deps == 50
        assert payload.commit_sha is None

    def test_check_deps_defaults_to_empty(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        assert payload.check_deps == []

    def test_exclude_deps_defaults_to_empty(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        assert payload.exclude_deps == []

    def test_mode_defaults_to_normal(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        assert payload.mode == OutdatedCheckMode.NORMAL

    def test_include_prerelease_defaults_to_false(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        assert payload.include_prerelease is False

    def test_check_deps_accepted(self) -> None:
        payload = ScanWebhookPayload(
            repo_url="https://github.com/x/y",
            check_deps=["requests", "flask"],
        )
        assert payload.check_deps == ["requests", "flask"]

    def test_repo_url_required(self) -> None:
        with pytest.raises(ValidationError):
            ScanWebhookPayload()  # type: ignore[call-arg]

    def test_max_deps_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ScanWebhookPayload(repo_url="https://github.com/x/y", max_deps=0)


class TestDependency:
    def test_construction(self) -> None:
        dep = Dependency(
            name="requests",
            current_version="2.28.0",
            ecosystem=Ecosystem.PYTHON,
            manifest_path="requirements.txt",
        )
        assert dep.name == "requests"
        assert dep.current_version == "2.28.0"
        assert dep.ecosystem == Ecosystem.PYTHON
        assert dep.manifest_path == "requirements.txt"

    def test_all_fields_required(self) -> None:
        with pytest.raises(ValidationError):
            Dependency(name="requests")  # type: ignore[call-arg]


class TestOutdatedDependency:
    def test_construction(self) -> None:
        dep = OutdatedDependency(
            name="requests",
            current_version="2.28.0",
            latest_version="2.31.0",
            ecosystem=Ecosystem.PYTHON,
            manifest_path="requirements.txt",
        )
        assert dep.name == "requests"
        assert dep.latest_version == "2.31.0"

    def test_optional_urls_default_none(self) -> None:
        dep = OutdatedDependency(
            name="requests",
            current_version="2.28.0",
            latest_version="2.31.0",
            ecosystem=Ecosystem.PYTHON,
            manifest_path="requirements.txt",
        )
        assert dep.homepage_url is None
        assert dep.repository_url is None
        assert dep.changelog_url is None

    def test_with_urls(self) -> None:
        dep = OutdatedDependency(
            name="requests",
            current_version="2.28.0",
            latest_version="2.31.0",
            ecosystem=Ecosystem.PYTHON,
            manifest_path="requirements.txt",
            homepage_url="https://requests.readthedocs.io",
            repository_url="https://github.com/psf/requests",
            changelog_url="https://github.com/psf/requests/blob/main/HISTORY.md",
        )
        assert dep.homepage_url == "https://requests.readthedocs.io"


class TestScanResult:
    def test_construction(self) -> None:
        dep = Dependency(
            name="flask",
            current_version="2.0.0",
            ecosystem=Ecosystem.PYTHON,
            manifest_path="requirements.txt",
        )
        outdated = OutdatedDependency(
            name="flask",
            current_version="2.0.0",
            latest_version="3.0.0",
            ecosystem=Ecosystem.PYTHON,
            manifest_path="requirements.txt",
        )
        result = ScanResult(
            all_deps=[dep],
            outdated=[outdated],
            manifests_found=["requirements.txt"],
            scan_duration_seconds=1.5,
        )
        assert len(result.all_deps) == 1
        assert len(result.outdated) == 1
        assert result.manifests_found == ["requirements.txt"]
        assert result.scan_duration_seconds == 1.5


class TestExecutionResult:
    def test_construction(self) -> None:
        result = ExecutionResult(
            command_run="pip install requests",
            exit_code=0,
            stdout="Successfully installed",
            stderr="",
        )
        assert result.command_run == "pip install requests"
        assert result.exit_code == 0

    def test_truncated_default_false(self) -> None:
        result = ExecutionResult(
            command_run="ls",
            exit_code=0,
            stdout="",
            stderr="",
        )
        assert result.truncated is False


class TestChangelogResult:
    def test_construction(self) -> None:
        result: ChangelogResult = {
            "content": "## 3.0.0\n- Breaking change",
            "source": "github_releases",
            "strategy_used": 1,
            "truncated": False,
            "format_warning": False,
        }
        assert result["content"] == "## 3.0.0\n- Breaking change"
        assert result["source"] == "github_releases"
        assert result["strategy_used"] == 1


class TestAnalysisReport:
    def test_construction(self) -> None:
        report = AnalysisReport(
            dependency_name="flask",
            is_breaking=True,
            error_summary="Removed deprecated API",
            changelog_citation="## 3.0.0 - Removed X",
            suggested_human_fix="Replace X with Y",
            confidence=0.85,
        )
        assert report.dependency_name == "flask"
        assert report.is_breaking is True
        assert report.confidence == 0.85

    def test_confidence_too_high(self) -> None:
        with pytest.raises(ValidationError):
            AnalysisReport(
                dependency_name="flask",
                is_breaking=False,
                error_summary="",
                changelog_citation="",
                suggested_human_fix="",
                confidence=1.5,
            )

    def test_confidence_too_low(self) -> None:
        with pytest.raises(ValidationError):
            AnalysisReport(
                dependency_name="flask",
                is_breaking=False,
                error_summary="",
                changelog_citation="",
                suggested_human_fix="",
                confidence=-0.1,
            )

    def test_confidence_boundary_values(self) -> None:
        report_zero = AnalysisReport(
            dependency_name="x",
            is_breaking=False,
            error_summary="",
            changelog_citation="",
            suggested_human_fix="",
            confidence=0.0,
        )
        assert report_zero.confidence == 0.0

        report_one = AnalysisReport(
            dependency_name="x",
            is_breaking=False,
            error_summary="",
            changelog_citation="",
            suggested_human_fix="",
            confidence=1.0,
        )
        assert report_one.confidence == 1.0


class TestScanAnalysisReport:
    def test_construction(self) -> None:
        scan_result = ScanResult(
            all_deps=[],
            outdated=[],
            manifests_found=["pyproject.toml"],
            scan_duration_seconds=0.5,
        )
        report = ScanAnalysisReport(
            repo_url="https://github.com/x/y",
            branch_name="main",
            scan_result=scan_result,
            reports=[],
            total_duration_seconds=2.0,
        )
        assert report.repo_url == "https://github.com/x/y"
        assert report.skipped == []
        assert report.total_duration_seconds == 2.0

    def test_skipped_default_empty(self) -> None:
        scan_result = ScanResult(
            all_deps=[],
            outdated=[],
            manifests_found=[],
            scan_duration_seconds=0.0,
        )
        report = ScanAnalysisReport(
            repo_url="https://github.com/x/y",
            branch_name="main",
            scan_result=scan_result,
            reports=[],
            total_duration_seconds=0.0,
        )
        assert report.skipped == []

    def test_json_round_trip(self) -> None:
        dep = Dependency(
            name="flask",
            current_version="2.0.0",
            ecosystem=Ecosystem.PYTHON,
            manifest_path="requirements.txt",
        )
        outdated = OutdatedDependency(
            name="flask",
            current_version="2.0.0",
            latest_version="3.0.0",
            ecosystem=Ecosystem.PYTHON,
            manifest_path="requirements.txt",
        )
        scan_result = ScanResult(
            all_deps=[dep],
            outdated=[outdated],
            manifests_found=["requirements.txt"],
            scan_duration_seconds=1.0,
        )
        analysis = AnalysisReport(
            dependency_name="flask",
            is_breaking=True,
            error_summary="Breaking",
            changelog_citation="see changelog",
            suggested_human_fix="upgrade",
            confidence=0.9,
        )
        report = ScanAnalysisReport(
            repo_url="https://github.com/x/y",
            branch_name="main",
            scan_result=scan_result,
            reports=[analysis],
            skipped=["setuptools"],
            total_duration_seconds=5.0,
        )
        json_str = report.model_dump_json()
        restored = ScanAnalysisReport.model_validate_json(json_str)
        assert restored.repo_url == report.repo_url
        assert restored.reports[0].dependency_name == "flask"
        assert restored.scan_result.outdated[0].latest_version == "3.0.0"


class TestPackageConfidence:
    def test_construction(self) -> None:
        pc = PackageConfidence(
            name="requests",
            confidence=0.85,
            reason="Error message directly references requests import",
        )
        assert pc.name == "requests"
        assert pc.confidence == 0.85
        assert "requests" in pc.reason

    def test_confidence_boundaries(self) -> None:
        pc_zero = PackageConfidence(name="x", confidence=0.0, reason="safe")
        assert pc_zero.confidence == 0.0
        pc_one = PackageConfidence(name="x", confidence=1.0, reason="certain")
        assert pc_one.confidence == 1.0

    def test_confidence_too_high(self) -> None:
        with pytest.raises(ValidationError):
            PackageConfidence(name="x", confidence=1.5, reason="bad")

    def test_confidence_too_low(self) -> None:
        with pytest.raises(ValidationError):
            PackageConfidence(name="x", confidence=-0.1, reason="bad")


class TestMainExecutionAnalysis:
    def test_construction(self) -> None:
        analysis = MainExecutionAnalysis(
            packages_likely_breaking=[
                PackageConfidence(name="flask", confidence=0.9, reason="ImportError"),
            ],
            packages_likely_safe=["requests"],
            overall_test_passed=False,
            raw_error_summary="ImportError: cannot import name 'escape' from 'markupsafe'",
        )
        assert len(analysis.packages_likely_breaking) == 1
        assert analysis.packages_likely_breaking[0].name == "flask"
        assert analysis.packages_likely_safe == ["requests"]
        assert analysis.overall_test_passed is False

    def test_all_pass(self) -> None:
        analysis = MainExecutionAnalysis(
            packages_likely_breaking=[],
            packages_likely_safe=["requests", "flask"],
            overall_test_passed=True,
            raw_error_summary="",
        )
        assert analysis.overall_test_passed is True
        assert len(analysis.packages_likely_breaking) == 0


class TestJobState:
    def test_valid_values(self) -> None:
        assert JobState("pending") == JobState.PENDING
        assert JobState("running") == JobState.RUNNING
        assert JobState("completed") == JobState.COMPLETED
        assert JobState("failed") == JobState.FAILED

    def test_invalid_value(self) -> None:
        with pytest.raises(ValueError):
            JobState("cancelled")


class TestJobStatus:
    def test_construction(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        status = JobStatus(
            job_id="abc-123",
            state=JobState.PENDING,
            payload=payload,
        )
        assert status.job_id == "abc-123"
        assert status.state == JobState.PENDING
        assert status.result is None
        assert status.error is None
        assert status.created_at is not None
        assert status.updated_at is not None

    def test_with_result(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        scan_result = ScanResult(
            all_deps=[], outdated=[], manifests_found=[], scan_duration_seconds=0.0
        )
        report = ScanAnalysisReport(
            repo_url="https://github.com/x/y",
            branch_name="main",
            scan_result=scan_result,
            reports=[],
            total_duration_seconds=1.0,
        )
        status = JobStatus(
            job_id="abc-123",
            state=JobState.COMPLETED,
            payload=payload,
            result=report,
        )
        assert status.result is not None
        assert status.result.repo_url == "https://github.com/x/y"

    def test_with_error(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        status = JobStatus(
            job_id="abc-123",
            state=JobState.FAILED,
            payload=payload,
            error="Sandbox init failed",
        )
        assert status.error == "Sandbox init failed"


class TestWebhookAcceptedResponse:
    def test_construction(self) -> None:
        resp = WebhookAcceptedResponse(
            job_id="abc-123",
            status_url="/jobs/abc-123",
        )
        assert resp.job_id == "abc-123"
        assert resp.status_url == "/jobs/abc-123"


class TestScanWebhookPayloadGitFields:
    def test_commit_sha_defaults_to_none(self) -> None:
        p = ScanWebhookPayload(repo_url="https://github.com/a/b")
        assert p.commit_sha is None

    def test_commit_sha_accepted(self) -> None:
        p = ScanWebhookPayload(
            repo_url="https://github.com/a/b",
            commit_sha="abc123def456",
        )
        assert p.commit_sha == "abc123def456"

    def test_git_provider_defaults_to_github(self) -> None:
        p = ScanWebhookPayload(repo_url="https://github.com/a/b")
        assert p.git_provider == "github"

    def test_git_provider_gitlab_accepted(self) -> None:
        p = ScanWebhookPayload(repo_url="https://gitlab.com/a/b", git_provider="gitlab")
        assert p.git_provider == "gitlab"

    def test_git_provider_unknown_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScanWebhookPayload(repo_url="https://bitbucket.org/a/b", git_provider="bitbucket")
