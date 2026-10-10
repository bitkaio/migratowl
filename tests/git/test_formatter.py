# SPDX-License-Identifier: Apache-2.0

"""Tests for PR comment formatter."""


from migratowl.git.formatter import format_pr_comment
from migratowl.models.schemas import (
    AnalysisReport,
    Ecosystem,
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
) -> ScanAnalysisReport:
    return ScanAnalysisReport(
        repo_url="https://github.com/org/repo",
        branch_name="main",
        scan_result=ScanResult(
            all_deps=[],
            outdated=[],
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

    def test_a_capped_run_says_why_packages_were_skipped(self) -> None:
        from migratowl.models.schemas import ModelCallBudget

        report = _make_report([], skipped=["boto3"])
        report.model_call_budget = ModelCallBudget(main=9, subagent=7)
        report.call_limit_reached = True

        comment = format_pr_comment(report)

        assert "model-call budget (9 calls," in comment
        assert "MIGRATOWL_MAX_MODEL_CALLS" in comment

    def test_an_uncapped_run_has_no_budget_note(self) -> None:
        comment = format_pr_comment(_make_report([], skipped=["boto3"]))

        assert "model-call budget" not in comment

    def test_skipped_packages_appear_in_details(self) -> None:
        comment = format_pr_comment(_make_report([], skipped=["boto3", "urllib3"]))
        assert "boto3" in comment
        assert "2 package" in comment

    def test_scan_duration_shown(self) -> None:
        comment = format_pr_comment(_make_report([], duration=47.3))
        assert "47.3s" in comment

    def test_long_fix_shows_short_summary_in_table_cell(self) -> None:
        long_fix = "Use the new API instead. See migration guide at https://example.com/migrate for full details."
        r = _make_analysis("pkg", is_breaking=True, fix=long_fix)
        comment = format_pr_comment(_make_report([r]))
        table_line = [ln for ln in comment.splitlines() if "`pkg`" in ln][0]
        fix_cell = table_line.split("|")[-2].strip()
        assert fix_cell.endswith("…")
        assert len(fix_cell) <= 85

    def test_breaking_packages_sorted_first(self) -> None:
        safe = _make_analysis("aaa", is_breaking=False)
        breaking = _make_analysis("zzz", is_breaking=True, fix="fix it")
        comment = format_pr_comment(_make_report([safe, breaking]))
        assert comment.index("zzz") < comment.index("aaa")

    def test_confidence_is_shown_as_a_percentage(self) -> None:
        r = _make_analysis("requests", is_breaking=False, confidence=0.9)
        comment = format_pr_comment(_make_report([r]))
        assert "| Confidence |" in comment
        assert "90%" in comment


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

    def test_claude_sonnet_5_pricing(self) -> None:
        from migratowl.git.formatter import _estimate_cost
        # claude-sonnet-5: $2/1M input, $10/1M output
        # 1M input = $3.00, 0.5M output = $7.50 → $10.50
        result = _estimate_cost("claude-sonnet-5", 1_000_000, 500_000)
        assert result == "~$7.00"

    def test_claude_opus_4_8_pricing(self) -> None:
        from migratowl.git.formatter import _estimate_cost
        # claude-opus-4-8: $5/1M input, $25/1M output
        # 1M input = $5.00, 0.5M output = $12.50 → $17.50
        result = _estimate_cost("claude-opus-4-8", 1_000_000, 500_000)
        assert result == "~$17.50"

    def test_claude_opus_4_7_pricing(self) -> None:
        from migratowl.git.formatter import _estimate_cost
        # claude-opus-4-7: $5/1M input, $25/1M output (Opus 4.6+ pricing)
        result = _estimate_cost("claude-opus-4-7", 1_000_000, 500_000)
        assert result == "~$17.50"

    def test_claude_haiku_4_5_pricing(self) -> None:
        from migratowl.git.formatter import _estimate_cost
        # claude-haiku-4-5: $1/1M input, $5/1M output
        # 1M input = $1.00, 1M output = $5.00 → $6.00
        result = _estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        assert result == "~$6.00"

    def test_claude_haiku_4_5_bare_alias(self) -> None:
        from migratowl.git.formatter import _estimate_cost
        # bare alias without the date suffix must also resolve
        result = _estimate_cost("claude-haiku-4-5", 1_000_000, 1_000_000)
        assert result == "~$6.00"

    def test_claude_fable_5_pricing(self) -> None:
        from migratowl.git.formatter import _estimate_cost
        # claude-fable-5: $10/1M input, $50/1M output
        # 1M input = $10.00, 0.5M output = $25.00 → $35.00
        result = _estimate_cost("claude-fable-5", 1_000_000, 500_000)
        assert result == "~$35.00"


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


class TestShortFix:
    def test_uses_first_sentence_when_short(self) -> None:
        from migratowl.git.formatter import _short_fix
        result = _short_fix("Pin to 2.x. See docs.")
        assert result == "Pin to 2.x…"

    def test_truncates_to_80_when_no_sentence_break(self) -> None:
        from migratowl.git.formatter import _short_fix
        long = "x" * 100
        result = _short_fix(long)
        assert result == "x" * 80 + "…"

    def test_no_ellipsis_when_fits(self) -> None:
        from migratowl.git.formatter import _short_fix
        result = _short_fix("Short fix.")
        assert result == "Short fix."


class TestFixDetails:
    def test_full_fix_appears_in_details_block(self) -> None:
        long_fix = "x" * 200
        r = _make_analysis("pkg", is_breaking=True, fix=long_fix)
        comment = format_pr_comment(_make_report([r]))
        assert long_fix in comment
        assert "Fix details" in comment

    def test_short_fix_not_truncated_in_table_cell(self) -> None:
        r = _make_analysis("pkg", is_breaking=True, fix="Pin to 2.x.")
        comment = format_pr_comment(_make_report([r]))
        table_line = [ln for ln in comment.splitlines() if "`pkg`" in ln][0]
        fix_cell = table_line.split("|")[-2].strip()
        assert fix_cell == "Pin to 2.x."

    def test_no_fix_details_block_for_all_safe_packages(self) -> None:
        r = _make_analysis("requests", is_breaking=False)
        comment = format_pr_comment(_make_report([r]))
        assert "Fix details" not in comment

    def test_no_fix_details_block_when_breaking_has_no_fix(self) -> None:
        r = _make_analysis("pkg", is_breaking=True, fix="")
        comment = format_pr_comment(_make_report([r]))
        assert "Fix details" not in comment

    def test_fix_details_contains_package_name(self) -> None:
        r = _make_analysis("httpx", is_breaking=True, fix="Use httpx.Client() instead of requests-style calls. " * 5)
        comment = format_pr_comment(_make_report([r]))
        assert "httpx" in comment.split("Fix details")[1]

    def test_first_sentence_as_inline_summary(self) -> None:
        fix = "Update imports. Full details at docs.example.com."
        r = _make_analysis("pkg", is_breaking=True, fix=fix)
        comment = format_pr_comment(_make_report([r]))
        table_line = [ln for ln in comment.splitlines() if "`pkg`" in ln][0]
        fix_cell = table_line.split("|")[-2].strip()
        assert fix_cell == "Update imports…"



class TestCurrentModelPricing:
    """Prices from Anthropic's model table (2026-09-25)."""

    def test_current_models_are_priced(self) -> None:
        from migratowl.git.formatter import _estimate_cost

        assert _estimate_cost("claude-opus-5-5", 1_000_000, 0) == "~$4.00"
        assert _estimate_cost("claude-sonnet-5-5", 1_000_000, 0) == "~$2.00"
        assert _estimate_cost("claude-fable-5-1", 0, 1_000_000) == "~$50.00"

    def test_cache_reads_priced_at_the_cache_rate(self) -> None:
        from migratowl.git.formatter import _estimate_cost

        # input_tokens includes cached tokens (langchain-anthropic folds them in)
        assert _estimate_cost("claude-sonnet-5-5", 1_000_000, 0, cache_read=1_000_000) == "~$0.20"

    def test_cache_writes_priced_at_one_and_a_quarter_input(self) -> None:
        from migratowl.git.formatter import _estimate_cost

        assert _estimate_cost("claude-sonnet-5-5", 1_000_000, 0, cache_creation=1_000_000) == "~$2.50"

    def test_comment_footer_uses_cache_counts(self) -> None:
        from migratowl.git.formatter import format_pr_comment
        from migratowl.models.schemas import ScanAnalysisReport, ScanResult

        report = ScanAnalysisReport(
            repo_url="r", branch_name="main",
            scan_result=ScanResult(all_deps=[], outdated=[], manifests_found=[], scan_duration_seconds=0),
            reports=[], total_duration_seconds=1.0, total_input_tokens=1_000_000, total_output_tokens=0,
            total_cache_read_tokens=1_000_000, model_name="claude-sonnet-5-5",
        )
        assert "~$0.20" in format_pr_comment(report)


class TestCommentEscaping:
    """LLM text (partly from untrusted changelogs) must not break or abuse the PR comment."""

    def _comment(self, **report_fields) -> str:
        from migratowl.git.formatter import format_pr_comment
        from migratowl.models.schemas import AnalysisReport, ScanAnalysisReport, ScanResult

        fields = {"dependency_name": "pkg", "is_breaking": True, "error_summary": "e",
                  "changelog_citation": "", "suggested_human_fix": "fix", "confidence": 0.9}
        fields.update(report_fields)
        return format_pr_comment(ScanAnalysisReport(
            repo_url="r", branch_name="main",
            scan_result=ScanResult(all_deps=[], outdated=[], manifests_found=[], scan_duration_seconds=0),
            reports=[AnalysisReport(**fields)], total_duration_seconds=1.0,
        ))

    def _table_row(self, comment: str) -> str:
        return next(line for line in comment.splitlines() if line.startswith("| `"))

    def test_pipes_and_newlines_cannot_break_the_table(self) -> None:
        row = self._table_row(self._comment(suggested_human_fix="Use a | b.\nThen | c"))
        assert row.count(" | ") == 3  # still exactly four cells
        assert "\\|" in row

    def test_mentions_do_not_ping_anyone(self) -> None:
        comment = self._comment(suggested_human_fix="Ask @octocat or @org/team. Mail a@b.com.")
        assert "@octocat" not in comment and "@org/team" not in comment
        assert "a@b.com" in comment  # email addresses stay readable

    def test_raw_html_is_escaped(self) -> None:
        comment = self._comment(suggested_human_fix='</details><img src="https://evil.example/x.png">')
        assert "<img" not in comment
        assert comment.count("</details>") == comment.count("<details>")

    def test_markdown_images_are_neutralised(self) -> None:
        comment = self._comment(suggested_human_fix="See ![pixel](https://evil.example/t.gif)")
        assert "![pixel](" not in comment

    def test_long_text_is_capped(self) -> None:
        comment = self._comment(suggested_human_fix="x" * 20_000)
        assert len(comment) < 5_000

    def test_backticks_in_names_cannot_escape_code_span(self) -> None:
        row = self._table_row(self._comment(dependency_name="evil`<b>x</b>`"))
        assert "<b>" not in row


class TestReviewInComment:
    """MO-65.4: the PR comment shows reviews and confidence."""

    @staticmethod
    def _report(reviews: dict):
        from migratowl.models.schemas import AnalysisReport, ScanAnalysisReport, ScanResult

        return ScanAnalysisReport(
            repo_url="https://x/y", branch_name="main",
            scan_result=ScanResult(all_deps=[], outdated=[], manifests_found=[], scan_duration_seconds=0),
            reports=[
                AnalysisReport(dependency_name="express", is_breaking=False, error_summary="", changelog_citation="",
                               suggested_human_fix="", confidence=0.9),
                AnalysisReport(dependency_name="ejs", is_breaking=False, error_summary="", changelog_citation="",
                               suggested_human_fix="", confidence=0.5),
            ],
            total_duration_seconds=1.0, reviews=reviews,
        )

    def test_review_status_reason_and_confidence(self) -> None:
        from migratowl.git.formatter import format_pr_comment

        comment = format_pr_comment(self._report({"express": "Express 5 no longer accepts ? in route paths "
                                                              "— found at app.js:4"}))

        assert "| Package | Status | Confidence | Note |" in comment
        assert "🔍 Review" in comment and "✅ Safe" in comment
        assert "90%" in comment and "50%" in comment
        assert "app.js:4" in comment
        assert "1 to review" in comment

    def test_without_reviews_everything_safe(self) -> None:
        from migratowl.git.formatter import format_pr_comment

        comment = format_pr_comment(self._report({}))

        assert "🔍 Review" not in comment and "all safe" in comment
