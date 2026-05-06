# SPDX-License-Identifier: Apache-2.0

"""Tests for PR comment formatter."""


from migratowl.git.formatter import format_pr_comment
from migratowl.models.schemas import (
    AnalysisReport,
    Dependency,
    Ecosystem,
    OutdatedDependency,
    RegistryFailure,
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
    registry_failures: list[RegistryFailure] | None = None,
    outdated: list[OutdatedDependency] | None = None,
    all_deps: list[Dependency] | None = None,
) -> ScanAnalysisReport:
    return ScanAnalysisReport(
        repo_url="https://github.com/org/repo",
        branch_name="main",
        scan_result=ScanResult(
            all_deps=all_deps or [],
            outdated=outdated or [],
            manifests_found=["requirements.txt"],
            scan_duration_seconds=1.0,
            registry_failures=registry_failures or [],
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


def _make_outdated(name: str, current: str, latest: str) -> OutdatedDependency:
    return OutdatedDependency(
        name=name,
        current_version=current,
        latest_version=latest,
        ecosystem=Ecosystem.NODEJS,
        manifest_path="package.json",
    )


def _make_dep(name: str, version: str) -> Dependency:
    return Dependency(
        name=name,
        current_version=version,
        ecosystem=Ecosystem.NODEJS,
        manifest_path="package.json",
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
        assert "✓" in comment
        assert "requests" in comment

    def test_breaking_package_shows_cross(self) -> None:
        r = _make_analysis("httpx", is_breaking=True, fix="Use httpx.Client() instead")
        comment = format_pr_comment(_make_report([r]))
        assert "✗" in comment
        assert "httpx" in comment
        assert "Use httpx.Client() instead" in comment

    def test_skipped_packages_appear_in_table(self) -> None:
        comment = format_pr_comment(_make_report([], skipped=["boto3", "urllib3"]))
        assert "boto3" in comment
        assert "urllib3" in comment

    def test_scan_duration_shown(self) -> None:
        comment = format_pr_comment(_make_report([], duration=47.3))
        assert "47.3s" in comment

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


class TestMetaLine:
    def test_meta_line_shows_repo_slug(self) -> None:
        comment = format_pr_comment(_make_report([]))
        assert "org/repo" in comment

    def test_meta_line_shows_branch(self) -> None:
        comment = format_pr_comment(_make_report([]))
        assert "main" in comment

    def test_meta_line_appears_before_table(self) -> None:
        r = _make_analysis("pkg", is_breaking=False)
        comment = format_pr_comment(_make_report([r]))
        assert comment.index("org/repo") < comment.index("Package")


class TestTableColumns:
    def test_table_header_has_four_columns(self) -> None:
        r = _make_analysis("requests", is_breaking=False)
        comment = format_pr_comment(_make_report([r]))
        header = next(ln for ln in comment.splitlines() if "Package" in ln)
        assert "Current" in header
        assert "Latest" in header
        assert "Status" in header

    def test_table_has_no_fix_column(self) -> None:
        r = _make_analysis("pkg", is_breaking=True, fix="some fix text")
        comment = format_pr_comment(_make_report([r]))
        header = next(ln for ln in comment.splitlines() if "Package" in ln)
        assert "Fix" not in header

    def test_current_version_shown_in_row(self) -> None:
        r = _make_analysis("requests", is_breaking=False)
        comment = format_pr_comment(_make_report(
            [r], outdated=[_make_outdated("requests", "2.28.0", "3.0.0")]
        ))
        row = next(ln for ln in comment.splitlines() if "`requests`" in ln and "|" in ln)
        assert "2.28.0" in row

    def test_latest_version_shown_in_row(self) -> None:
        r = _make_analysis("requests", is_breaking=False)
        comment = format_pr_comment(_make_report(
            [r], outdated=[_make_outdated("requests", "2.28.0", "3.0.0")]
        ))
        row = next(ln for ln in comment.splitlines() if "`requests`" in ln and "|" in ln)
        assert "3.0.0" in row

    def test_status_safe_symbol(self) -> None:
        r = _make_analysis("requests", is_breaking=False)
        comment = format_pr_comment(_make_report([r]))
        row = next(ln for ln in comment.splitlines() if "`requests`" in ln and "|" in ln)
        assert "✓ safe" in row

    def test_status_breaking_symbol(self) -> None:
        r = _make_analysis("httpx", is_breaking=True)
        comment = format_pr_comment(_make_report([r]))
        row = next(ln for ln in comment.splitlines() if "`httpx`" in ln and "|" in ln)
        assert "✗ breaking" in row

    def test_fix_text_not_in_table_row(self) -> None:
        r = _make_analysis("pkg", is_breaking=True, fix="unique fix phrase here")
        comment = format_pr_comment(_make_report([r]))
        row = next(ln for ln in comment.splitlines() if "`pkg`" in ln and "|" in ln)
        assert "unique fix phrase here" not in row

    def test_dash_shown_when_version_unknown(self) -> None:
        r = _make_analysis("pkg", is_breaking=False)
        comment = format_pr_comment(_make_report([r]))  # no outdated provided
        row = next(ln for ln in comment.splitlines() if "`pkg`" in ln and "|" in ln)
        assert "—" in row


class TestSkippedInTable:
    def test_skipped_package_is_a_table_row(self) -> None:
        comment = format_pr_comment(_make_report(
            [], skipped=["boto3 (already at latest: 1.34.0)"],
            all_deps=[_make_dep("boto3", "1.34.0")]
        ))
        rows = [ln for ln in comment.splitlines() if "`boto3`" in ln and "|" in ln]
        assert len(rows) == 1

    def test_skipped_status_label(self) -> None:
        comment = format_pr_comment(_make_report([], skipped=["boto3"]))
        row = next(ln for ln in comment.splitlines() if "`boto3`" in ln and "|" in ln)
        assert "skipped" in row.lower()

    def test_skipped_current_version_from_all_deps(self) -> None:
        comment = format_pr_comment(_make_report(
            [], skipped=["boto3"],
            all_deps=[_make_dep("boto3", "1.34.0")]
        ))
        row = next(ln for ln in comment.splitlines() if "`boto3`" in ln and "|" in ln)
        assert "1.34.0" in row

    def test_no_separate_skipped_details_block(self) -> None:
        comment = format_pr_comment(_make_report([], skipped=["boto3", "urllib3"]))
        assert "package(s) skipped" not in comment

    def test_skipped_rows_after_analyzed_rows(self) -> None:
        r = _make_analysis("aaa", is_breaking=False)
        comment = format_pr_comment(_make_report([r], skipped=["zzz-skipped"]))
        assert comment.index("aaa") < comment.index("zzz-skipped")

    def test_latest_version_shown_when_already_at_latest(self) -> None:
        comment = format_pr_comment(_make_report(
            [],
            skipped=["mongoose (already at latest: 9.6.1)"],
            all_deps=[_make_dep("mongoose", "9.6.1")],
        ))
        row = next(ln for ln in comment.splitlines() if "`mongoose`" in ln and "|" in ln)
        assert row.count("9.6.1") == 2  # both Current and Latest columns

    def test_latest_dash_when_reason_is_unknown(self) -> None:
        comment = format_pr_comment(_make_report([], skipped=["boto3 (excluded by config)"]))
        row = next(ln for ln in comment.splitlines() if "`boto3`" in ln and "|" in ln)
        assert "— |" in row  # Latest column is dash


class TestSkippedDetailCards:
    def test_skipped_package_has_detail_card(self) -> None:
        comment = format_pr_comment(_make_report([], skipped=["boto3 (already at latest: 1.34.0)"]))
        assert "<details>" in comment

    def test_skipped_card_summary_contains_package_name(self) -> None:
        comment = format_pr_comment(_make_report([], skipped=["boto3 (already at latest: 1.34.0)"]))
        summary = next(ln for ln in comment.splitlines() if "boto3" in ln and "<summary>" in ln)
        assert "boto3" in summary

    def test_skipped_card_body_contains_reason(self) -> None:
        comment = format_pr_comment(_make_report([], skipped=["boto3 (already at latest: 1.34.0)"]))
        assert "already at latest: 1.34.0" in comment

    def test_one_card_per_skipped_package(self) -> None:
        comment = format_pr_comment(_make_report(
            [], skipped=["boto3 (already at latest: 1.0.0)", "urllib3 (excluded by config)"]
        ))
        assert comment.count("<details>") == 2

    def test_skipped_cards_after_breaking_cards(self) -> None:
        r = _make_analysis("aaa", is_breaking=True)
        comment = format_pr_comment(_make_report([r], skipped=["zzz (already at latest: 1.0.0)"]))
        assert comment.index("aaa") < comment.index("zzz")

    def test_no_skipped_card_when_no_skipped(self) -> None:
        r = _make_analysis("requests", is_breaking=False)
        comment = format_pr_comment(_make_report([r]))
        assert "<details>" not in comment


class TestDetailCards:
    def test_breaking_package_has_detail_card(self) -> None:
        r = _make_analysis("httpx", is_breaking=True)
        comment = format_pr_comment(_make_report([r]))
        assert "<details>" in comment

    def test_detail_card_contains_error_summary(self) -> None:
        r = _make_analysis("httpx", is_breaking=True)
        comment = format_pr_comment(_make_report([r]))
        assert "ImportError" in comment

    def test_detail_card_contains_changelog_citation(self) -> None:
        r = _make_analysis("httpx", is_breaking=True)
        comment = format_pr_comment(_make_report([r]))
        assert "3.0.0 removed X" in comment

    def test_detail_card_contains_full_fix_text(self) -> None:
        long_fix = "Replace X with Y and update all call sites thoroughly."
        r = _make_analysis("httpx", is_breaking=True, fix=long_fix)
        comment = format_pr_comment(_make_report([r]))
        assert long_fix in comment

    def test_no_detail_card_for_safe_only(self) -> None:
        r = _make_analysis("requests", is_breaking=False)
        comment = format_pr_comment(_make_report([r]))
        assert "<details>" not in comment

    def test_detail_card_shows_version_range_in_summary(self) -> None:
        r = _make_analysis("express", is_breaking=True)
        comment = format_pr_comment(_make_report(
            [r], outdated=[_make_outdated("express", "4.21.2", "5.1.0")]
        ))
        summary_line = next(
            ln for ln in comment.splitlines() if "express" in ln and "<summary>" in ln
        )
        assert "4.21.2" in summary_line
        assert "5.1.0" in summary_line

    def test_one_card_per_breaking_package(self) -> None:
        r1 = _make_analysis("express", is_breaking=True)
        r2 = _make_analysis("lodash", is_breaking=True)
        comment = format_pr_comment(_make_report([r1, r2]))
        assert comment.count("<details>") == 2

    def test_no_card_when_breaking_has_no_content(self) -> None:
        r = AnalysisReport(
            dependency_name="pkg",
            is_breaking=True,
            error_summary="",
            changelog_citation="",
            suggested_human_fix="",
            confidence=0.5,
        )
        comment = format_pr_comment(_make_report([r]))
        # Card still exists for the breaking package, just with minimal content
        assert "<details>" in comment
        assert "`pkg`" in comment


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
        result = _estimate_cost("claude-sonnet-4-6", 1_000_000, 500_000)
        assert result == "~$10.50"

    def test_cost_rounds_to_two_decimal_places(self) -> None:
        from migratowl.git.formatter import _estimate_cost
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


class TestFormatPrCommentRegistryFailures:
    def test_failures_block_shown_when_present(self) -> None:
        failures = [RegistryFailure(name="mongoose", ecosystem=Ecosystem.NODEJS)]
        comment = format_pr_comment(_make_report([], registry_failures=failures))
        assert "mongoose" in comment
        assert "could not be queried" in comment
        assert "<details>" in comment

    def test_no_failures_block_when_empty(self) -> None:
        comment = format_pr_comment(_make_report([]))
        assert "could not be queried" not in comment

    def test_failure_count_in_summary_line(self) -> None:
        failures = [
            RegistryFailure(name="mongoose", ecosystem=Ecosystem.NODEJS),
            RegistryFailure(name="@popperjs/core", ecosystem=Ecosystem.NODEJS),
        ]
        comment = format_pr_comment(_make_report([], registry_failures=failures))
        assert "2 package(s)" in comment
