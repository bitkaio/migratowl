# SPDX-License-Identifier: Apache-2.0

"""Tests for webhook helper functions."""

from langchain_core.messages import AIMessage, HumanMessage

from migratowl.api.helpers import _accumulate_tokens
from migratowl.models.schemas import Ecosystem, ScanWebhookPayload


class TestAccumulateTokens:
    def test_returns_zeros_for_empty_messages(self) -> None:
        assert _accumulate_tokens([]) == (0, 0)

    def test_sums_usage_metadata_from_ai_messages(self) -> None:
        msgs = [
            AIMessage(content="hello", usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}),
            AIMessage(content="world", usage_metadata={"input_tokens": 200, "output_tokens": 80, "total_tokens": 280}),
        ]
        assert _accumulate_tokens(msgs) == (300, 130)

    def test_ignores_non_ai_messages(self) -> None:
        msgs = [
            HumanMessage(content="scan this"),
            AIMessage(content="ok", usage_metadata={"input_tokens": 50, "output_tokens": 20, "total_tokens": 70}),
        ]
        assert _accumulate_tokens(msgs) == (50, 20)

    def test_ignores_ai_messages_without_usage_metadata(self) -> None:
        msgs = [AIMessage(content="no metadata here")]
        assert _accumulate_tokens(msgs) == (0, 0)

    def test_handles_dict_messages(self) -> None:
        msgs = [{"role": "assistant", "content": "dict message"}]
        assert _accumulate_tokens(msgs) == (0, 0)


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
        report = assemble_report(payload, prepared, [_verdict("flask", True)], duration=12.34, tokens=(100, 20))

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
        report = assemble_report(payload, prepared, reports, duration=0, tokens=(0, 0))

        assert [r.dependency_name for r in report.reports] == ["Flask"]
        assert report.reports[0].is_breaking is True
        assert report.skipped == ["requests"]
