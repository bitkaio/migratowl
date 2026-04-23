# SPDX-License-Identifier: Apache-2.0

"""Tests for PR comment formatter."""


from migratowl.git.formatter import format_pr_comment
from migratowl.models.schemas import (
    AnalysisReport,
    ScanAnalysisReport,
    ScanResult,
)


def _make_report(
    reports: list[AnalysisReport],
    skipped: list[str] | None = None,
    duration: float = 12.5,
) -> ScanAnalysisReport:
    return ScanAnalysisReport(
        repo_url="https://github.com/org/repo",
        branch_name="main",
        scan_result=ScanResult(
            all_deps=[],
            outdated=[],
            manifests_found=["requirements.txt"],
            scan_duration_seconds=1.0,
        ),
        reports=reports,
        skipped=skipped or [],
        total_duration_seconds=duration,
    )


def _make_analysis(
    name: str,
    is_breaking: bool,
    fix: str = "",
    confidence: float = 0.9,
) -> AnalysisReport:
    return AnalysisReport(
        dependency_name=name,
        is_breaking=is_breaking,
        error_summary="ImportError" if is_breaking else "",
        changelog_citation="3.0.0 removed X" if is_breaking else "",
        suggested_human_fix=fix,
        confidence=confidence,
    )


class TestFormatPrComment:
    def test_contains_header(self) -> None:
        comment = format_pr_comment(_make_report([]))
        assert "## Migratowl Dependency Analysis" in comment

    def test_no_reports_shows_empty_message(self) -> None:
        comment = format_pr_comment(_make_report([]))
        assert "No outdated dependencies" in comment

    def test_safe_package_shows_checkmark(self) -> None:
        r = _make_analysis("requests", is_breaking=False)
        comment = format_pr_comment(_make_report([r]))
        assert "✅" in comment
        assert "requests" in comment

    def test_breaking_package_shows_warning(self) -> None:
        r = _make_analysis("httpx", is_breaking=True, fix="Use httpx.Client() instead")
        comment = format_pr_comment(_make_report([r]))
        assert "⚠️" in comment
        assert "httpx" in comment
        assert "Use httpx.Client() instead" in comment

    def test_skipped_packages_appear_in_details(self) -> None:
        comment = format_pr_comment(_make_report([], skipped=["boto3", "urllib3"]))
        assert "boto3" in comment
        assert "2 package" in comment

    def test_scan_duration_shown(self) -> None:
        comment = format_pr_comment(_make_report([], duration=47.3))
        assert "47.3s" in comment

    def test_long_fix_is_truncated(self) -> None:
        long_fix = "x" * 200
        r = _make_analysis("pkg", is_breaking=True, fix=long_fix)
        comment = format_pr_comment(_make_report([r]))
        table_line = [ln for ln in comment.splitlines() if "pkg" in ln][0]
        fix_cell = table_line.split("|")[-2].strip()
        assert len(fix_cell) <= 120

    def test_breaking_packages_sorted_first(self) -> None:
        safe = _make_analysis("aaa", is_breaking=False)
        breaking = _make_analysis("zzz", is_breaking=True, fix="fix it")
        comment = format_pr_comment(_make_report([safe, breaking]))
        assert comment.index("zzz") < comment.index("aaa")

    def test_confidence_not_shown_in_output(self) -> None:
        r = _make_analysis("requests", is_breaking=False, confidence=0.9)
        comment = format_pr_comment(_make_report([r]))
        assert "Confidence" not in comment
        assert "90%" not in comment