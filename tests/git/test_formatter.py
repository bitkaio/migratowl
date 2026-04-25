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
    input_tokens: int = 0,
    output_tokens: int = 0,
    model_name: str = "",
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
        total_input_tokens=input_tokens,
        total_output_tokens=output_tokens,
        model_name=model_name,
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


class TestFormatTokens:
    def test_zero_tokens_returns_empty_string(self) -> None:
        from migratowl.git.formatter import _format_tokens
        assert _format_tokens(0, 0) == ""

    def test_shows_m_for_millions(self) -> None:
        from migratowl.git.formatter import _format_tokens
        result = _format_tokens(1_500_000, 500_000)
        assert "2.0M" in result

    def test_shows_input_and_output_breakdown(self) -> None:
        from migratowl.git.formatter import _format_tokens
        result = _format_tokens(900_000, 300_000)
        assert "↑" in result
        assert "↓" in result


class TestEstimateCost:
    def test_returns_empty_for_unknown_model(self) -> None:
        from migratowl.git.formatter import _estimate_cost
        assert _estimate_cost("unknown-model-xyz", 1_000_000, 500_000) == ""

    def test_returns_empty_when_no_tokens(self) -> None:
        from migratowl.git.formatter import _estimate_cost
        assert _estimate_cost("claude-sonnet-4-6", 0, 0) == ""

    def test_calculates_cost_for_known_model(self) -> None:
        from migratowl.git.formatter import _estimate_cost
        # claude-sonnet-4-6: $3/1M input, $15/1M output
        # 1M input = $3.00, 0.5M output = $7.50 → $10.50
        result = _estimate_cost("claude-sonnet-4-6", 1_000_000, 500_000)
        assert result == "~$10.50"

    def test_cost_rounds_to_two_decimal_places(self) -> None:
        from migratowl.git.formatter import _estimate_cost
        # $0.30 input + $1.50 output = $1.80
        result = _estimate_cost("claude-sonnet-4-6", 100_000, 100_000)
        assert result == "~$1.80"


class TestFormatPrCommentTokenFooter:
    def test_footer_shows_tokens_when_present(self) -> None:
        report = _make_report([], duration=10.0, input_tokens=900_000, output_tokens=300_000)
        comment = format_pr_comment(report)
        assert "↑" in comment
        assert "↓" in comment

    def test_footer_shows_cost_for_known_model(self) -> None:
        report = _make_report([], duration=10.0, input_tokens=1_000_000, output_tokens=500_000, model_name="claude-sonnet-4-6")
        comment = format_pr_comment(report)
        assert "~$10.50" in comment

    def test_footer_omits_cost_for_unknown_model(self) -> None:
        report = _make_report([], duration=10.0, input_tokens=500_000, output_tokens=200_000, model_name="unknown-future-model")
        comment = format_pr_comment(report)
        assert "~$" not in comment
        assert "↑" in comment

    def test_footer_omits_token_section_when_zero(self) -> None:
        report = _make_report([], duration=10.0)
        comment = format_pr_comment(report)
        assert "↑" not in comment
        assert "~$" not in comment