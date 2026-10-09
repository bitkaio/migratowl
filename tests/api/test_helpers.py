# SPDX-License-Identifier: Apache-2.0

"""Tests for webhook helper functions."""

from langchain_core.messages import AIMessage

from migratowl.api.helpers import TokenUsage, sum_usage
from migratowl.models.schemas import Ecosystem, ScanWebhookPayload


class TestSumUsage:
    def test_returns_zeros_when_nothing_ran(self) -> None:
        assert sum_usage([]) == TokenUsage()

    def test_sums_every_model_call(self) -> None:
        usages = [
            {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            {"input_tokens": 200, "output_tokens": 80, "total_tokens": 280},
        ]
        assert sum_usage(usages) == TokenUsage(input=300, output=130)

    def test_sums_cache_details(self) -> None:
        usages = [
            {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105,
             "input_token_details": {"cache_read": 60, "cache_creation": 10}},
            {"input_tokens": 50, "output_tokens": 5, "total_tokens": 55,
             "input_token_details": {"cache_read": None}},
        ]
        assert sum_usage(usages) == TokenUsage(input=150, output=10, cache_read=60, cache_creation=10)


def _verdict(name: str, breaking: bool = False):
    from migratowl.models.schemas import AnalysisReport

    return AnalysisReport(dependency_name=name, is_breaking=breaking, error_summary="", changelog_citation="",
                          suggested_human_fix="", confidence=0.9)


class TestExtractVerdicts:
    def test_reads_structured_response(self) -> None:
        from migratowl.api.helpers import extract_verdicts
        from migratowl.models.schemas import PackageVerdicts

        result = {"structured_response": PackageVerdicts(reports=[_verdict("flask", True)]), "messages": []}

        assert [r.dependency_name for r in extract_verdicts(result)] == ["flask"]

    def test_falls_back_to_json_in_messages(self) -> None:
        import json

        from migratowl.api.helpers import extract_verdicts

        content = json.dumps({"reports": [_verdict("flask").model_dump()]})
        result = {"messages": [AIMessage(content=content)]}

        assert extract_verdicts(result)[0].dependency_name == "flask"

    def test_raises_when_nothing_parseable(self) -> None:
        import pytest

        from migratowl.api.helpers import ReportExtractionError, extract_verdicts

        with pytest.raises(ReportExtractionError):
            extract_verdicts({"messages": [AIMessage(content="wrote it to /tmp/r.json")]})


class TestAssembleReport:
    def _prepared(self, names: list[str], skipped: list[str] | None = None):
        from migratowl.models.schemas import OutdatedDependency, ScanResult
        from migratowl.pipeline import PreparedScan

        deps = [OutdatedDependency(name=n, current_version="1.0", latest_version="2.0", ecosystem=Ecosystem.PYTHON,
                                   manifest_path="pyproject.toml") for n in names]
        return PreparedScan(
            scan_result=ScanResult(
                all_deps=[], outdated=deps, manifests_found=["pyproject.toml"], scan_duration_seconds=1.0
            ),
            candidates=deps,
            skipped=skipped or [],
        )

    def test_scan_result_and_ids_come_from_code(self) -> None:
        from migratowl.api.helpers import assemble_report

        payload = ScanWebhookPayload(repo_url="https://x/y", branch_name="dev")
        prepared = self._prepared(["flask"], skipped=["click"])
        report = assemble_report(payload, prepared, [_verdict("flask", True)], duration=12.34, tokens=TokenUsage(input=100, output=20))

        assert report.repo_url == "https://x/y" and report.branch_name == "dev"
        assert report.scan_result.manifests_found == ["pyproject.toml"]
        assert report.skipped == ["click"]
        assert report.total_duration_seconds == 12.3
        assert (report.total_input_tokens, report.total_output_tokens) == (100, 20)

    def test_canonical_names_drop_extras_and_skip_missing(self) -> None:
        from migratowl.api.helpers import assemble_report

        prepared = self._prepared(["Flask", "requests"])
        reports = [_verdict("flask", True), _verdict("FLASK"), _verdict("made-up")]
        payload = ScanWebhookPayload(repo_url="https://x/y")
        report = assemble_report(payload, prepared, reports, duration=0, tokens=TokenUsage())

        assert [r.dependency_name for r in report.reports] == ["Flask"]
        assert report.reports[0].is_breaking is True
        assert report.skipped == ["requests"]


class TestAssembleDuplicateNames:
    def test_breaking_verdict_wins_and_name_reported_once(self) -> None:
        from migratowl.api.helpers import assemble_report
        from migratowl.models.schemas import Ecosystem, OutdatedDependency, ScanResult
        from migratowl.pipeline import PreparedScan

        deps = [
            OutdatedDependency(name="lodash", current_version=v, latest_version="4.17.21", ecosystem=Ecosystem.NODEJS,
                               manifest_path=m)
            for v, m in (("^4.17.0", "a/package.json"), ("^3.10.0", "b/package.json"))
        ]
        prepared = PreparedScan(
            scan_result=ScanResult(all_deps=[], outdated=deps, manifests_found=[], scan_duration_seconds=0.0),
            candidates=deps,
            skipped=[],
        )
        safe = _verdict("lodash").model_copy(update={"confidence": 1.0})
        report = assemble_report(
            ScanWebhookPayload(repo_url="https://x/y"), prepared, [safe, _verdict("lodash", True)],
            duration=0, tokens=TokenUsage(),
        )

        assert [(r.dependency_name, r.is_breaking) for r in report.reports] == [("lodash", True)]
        assert report.skipped == []


class TestCitationFallback:
    def _prepared(self, excerpts: dict):
        from migratowl.models.schemas import Ecosystem, OutdatedDependency, ScanResult
        from migratowl.pipeline import PreparedScan

        dep = OutdatedDependency(name="express", current_version="4.0.0", latest_version="5.0.0",
                                 ecosystem=Ecosystem.NODEJS, manifest_path="package.json")
        return PreparedScan(
            scan_result=ScanResult(all_deps=[], outdated=[dep], manifests_found=[], scan_duration_seconds=0),
            candidates=[dep], skipped=[], changelog_excerpts=excerpts,
        )

    def test_empty_citation_filled_from_fetched_changelog(self) -> None:
        from migratowl.api.helpers import assemble_report

        report = assemble_report(ScanWebhookPayload(repo_url="r"), self._prepared({"express": "5.0.0\nremoved x"}),
                                 [_verdict("express")], duration=0, tokens=TokenUsage())
        assert report.reports[0].changelog_citation == "5.0.0\nremoved x"

    def test_model_citation_is_kept(self) -> None:
        from migratowl.api.helpers import assemble_report

        verdict = _verdict("express").model_copy(update={"changelog_citation": "## 5.0.0 removed x"})
        report = assemble_report(ScanWebhookPayload(repo_url="r"), self._prepared({"express": "other"}),
                                 [verdict], duration=0, tokens=TokenUsage())
        assert report.reports[0].changelog_citation == "## 5.0.0 removed x"
