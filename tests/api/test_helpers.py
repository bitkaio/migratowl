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

    def test_per_ttl_cache_writes_are_counted(self) -> None:
        """MO-100: langchain-anthropic zeroes cache_creation and reports writes per TTL instead."""
        usages = [{"input_tokens": 1000, "output_tokens": 5, "total_tokens": 1005,
                   "input_token_details": {"cache_read": 600, "cache_creation": 0,
                                           "ephemeral_5m_input_tokens": 300, "ephemeral_1h_input_tokens": 50}}]

        assert sum_usage(usages) == TokenUsage(input=1000, output=5, cache_read=600, cache_creation=350)


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


class TestBudgetCappedRuns:
    """MO-65.2: a run ended by the model-call cap keeps finished verdicts and fails nothing."""

    LIMIT = "Model call limits exceeded: run limit (30/30)"

    def test_capped_run_without_verdicts_returns_none(self) -> None:
        from langchain_core.messages import AIMessage, HumanMessage

        from migratowl.api.helpers import extract_verdicts

        assert extract_verdicts({"messages": [HumanMessage("brief"), AIMessage(self.LIMIT)]}) == []

    def test_capped_run_keeps_subagent_verdicts(self) -> None:
        import json

        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        from migratowl.api.helpers import extract_verdicts

        verdict = {"dependency_name": "express", "is_breaking": True, "error_summary": "routes",
                   "changelog_citation": "5.0.0 ...", "suggested_human_fix": "use req.query", "confidence": 0.8}
        state = {"messages": [
            HumanMessage("brief"),
            ToolMessage(content=json.dumps(verdict), tool_call_id="t1", name="task"),
            ToolMessage(content="not json", tool_call_id="t2", name="fetch_changelog_tool"),
            AIMessage(self.LIMIT),
        ]}

        reports = extract_verdicts(state)

        assert [(r.dependency_name, r.is_breaking) for r in reports] == [("express", True)]

    def test_call_limit_reached_only_for_the_agent_itself(self) -> None:
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        from migratowl.api.helpers import call_limit_reached

        assert call_limit_reached({"messages": [HumanMessage("brief"), AIMessage(self.LIMIT)]})
        # A subagent hitting its own cap reaches the agent as a tool result; the agent itself went on.
        sub = ToolMessage(content=self.LIMIT, tool_call_id="t1", name="task")
        assert not call_limit_reached({"messages": [HumanMessage("brief"), sub, AIMessage("{}")]})
        assert not call_limit_reached({})

    def test_an_uncapped_run_without_a_report_still_fails(self) -> None:
        import pytest
        from langchain_core.messages import AIMessage, HumanMessage

        from migratowl.api.helpers import ReportExtractionError, extract_verdicts

        with pytest.raises(ReportExtractionError):
            extract_verdicts({"messages": [HumanMessage("brief"), AIMessage("I am done.")]})


class TestReviewFlags:
    """MO-65.4: code, not the model, flags a 'safe' verdict that the evidence does not support."""

    @staticmethod
    def _prepared(evidence: dict, excerpts: dict | None = None, latest: str = "5.2.1"):
        from migratowl.models.schemas import OutdatedDependency, ScanResult
        from migratowl.pipeline import PreparedScan

        dep = OutdatedDependency(name="express", current_version="4.21.2", latest_version=latest,
                                 ecosystem="nodejs", manifest_path="package.json")
        return PreparedScan(
            scan_result=ScanResult(all_deps=[], outdated=[dep], manifests_found=[], scan_duration_seconds=0),
            candidates=[dep], skipped=[], evidence=evidence, changelog_excerpts=excerpts or {},
        )

    @staticmethod
    def _verdict(is_breaking: bool = False, citation: str = ""):
        from migratowl.models.schemas import AnalysisReport

        return AnalysisReport(dependency_name="express", is_breaking=is_breaking, error_summary="",
                              changelog_citation=citation, suggested_human_fix="", confidence=0.9)

    @staticmethod
    def _assemble(prepared, verdicts):
        from migratowl.api.helpers import TokenUsage, assemble_report
        from migratowl.models.schemas import ScanWebhookPayload

        return assemble_report(ScanWebhookPayload(repo_url="https://x/y"), prepared, verdicts,
                               duration=1.0, tokens=TokenUsage())

    @staticmethod
    def _evidence(tests_reach, hits=()):
        from migratowl.models.schemas import EvidenceHit, PackageEvidence

        return PackageEvidence(importing_files=["app.js"], importing_count=1, tests_reach=tests_reach,
                               hits=[EvidenceHit(**h) for h in hits])

    HIT = {"rule": "express5-route-path-syntax", "note": "Express 5 no longer accepts ? in route paths",
           "file": "app.js", "line": 4, "text": "app.get('/?search=:query', h)"}

    def test_a_breaking_pattern_in_the_code_overrides_safe(self) -> None:
        report = self._assemble(self._prepared({"express": self._evidence(True, [self.HIT])}), [self._verdict()])

        reason = report.reviews["express"]
        assert "Express 5 no longer accepts ? in route paths" in reason and "app.js:4" in reason
        assert report.evidence["express"].hits[0].file == "app.js"

    def test_major_bump_resting_on_tests_that_never_reach_it(self) -> None:
        report = self._assemble(self._prepared({"express": self._evidence(False)}), [self._verdict()])

        assert "no test reaches" in report.reviews["express"]

    def test_changelog_evidence_or_reaching_tests_keep_safe(self) -> None:
        with_citation = self._assemble(self._prepared({"express": self._evidence(False)}),
                                       [self._verdict(citation="5.0.0: nothing relevant")])
        reached = self._assemble(self._prepared({"express": self._evidence(True)}), [self._verdict()])
        minor = self._assemble(self._prepared({"express": self._evidence(False)}, latest="4.22.0"), [self._verdict()])

        assert with_citation.reviews == {} and reached.reviews == {} and minor.reviews == {}

    def test_breaking_verdicts_and_missing_evidence_are_not_flagged(self) -> None:
        breaking = self._assemble(self._prepared({"express": self._evidence(False, [self.HIT])}),
                                  [self._verdict(is_breaking=True)])
        unknown = self._assemble(self._prepared({}), [self._verdict()])

        assert breaking.reviews == {} and unknown.reviews == {}


class TestReviewFlagsAfterFailedValidation:
    """MO-83: a bare 'safe' on a major bump whose ecosystem's upgraded tests failed needs review."""

    @staticmethod
    def _prepared(passed: bool, latest: str = "2.1.4", evidence: dict | None = None):
        from migratowl.models.schemas import OutdatedDependency, ScanResult
        from migratowl.pipeline import EcosystemValidation, PreparedScan

        dep = OutdatedDependency(name="sqlalchemy", current_version="1.4.54", latest_version=latest,
                                 ecosystem="python", manifest_path="requirements.txt")
        return PreparedScan(
            scan_result=ScanResult(all_deps=[], outdated=[dep], manifests_found=[], scan_duration_seconds=0),
            candidates=[dep], skipped=[], evidence=evidence or {},
            validations=[EcosystemValidation(ecosystem="python", passed=passed, failed_step=None if passed else "test")],
        )

    @staticmethod
    def _verdict(citation: str = ""):
        from migratowl.models.schemas import AnalysisReport

        return AnalysisReport(dependency_name="sqlalchemy", is_breaking=False, error_summary="",
                              changelog_citation=citation, suggested_human_fix="", confidence=0.9)

    def test_uncited_safe_major_after_failed_tests_is_flagged(self) -> None:
        report = TestReviewFlags._assemble(self._prepared(passed=False), [self._verdict()])

        assert "tests failed" in report.reviews["sqlalchemy"]

    def test_a_citation_filled_in_from_the_excerpt_does_not_count(self) -> None:
        """The report backs an empty citation with the fetched excerpt; the model still cited nothing."""
        prepared = self._prepared(passed=False)
        prepared.changelog_excerpts = {"sqlalchemy": "2.0\nWhat's New in SQLAlchemy 2.0"}

        report = TestReviewFlags._assemble(prepared, [self._verdict()])

        assert "tests failed" in report.reviews["sqlalchemy"]
        assert report.reports[0].changelog_citation.startswith("2.0")

    def test_cited_minor_or_green_cases_are_not_flagged(self) -> None:
        cited = TestReviewFlags._assemble(self._prepared(passed=False), [self._verdict("2.0.0: Engine.execute kept")])
        green = TestReviewFlags._assemble(self._prepared(passed=True), [self._verdict()])
        minor = TestReviewFlags._assemble(self._prepared(passed=False, latest="1.4.60"), [self._verdict()])

        assert cited.reviews == {} and green.reviews == {} and minor.reviews == {}

class TestLowConfidenceReview:
    """MO-87: a safe verdict below the confidence threshold is not shown as plainly safe."""

    @staticmethod
    def _assemble(confidence: float, threshold: float | None):
        from migratowl.api.helpers import TokenUsage, assemble_report
        from migratowl.models.schemas import AnalysisReport, OutdatedDependency, ScanResult, ScanWebhookPayload
        from migratowl.pipeline import PreparedScan

        dep = OutdatedDependency(name="click", current_version="7.1.2", latest_version="8.5.0",
                                 ecosystem="python", manifest_path="requirements.txt")
        prepared = PreparedScan(
            scan_result=ScanResult(all_deps=[], outdated=[dep], manifests_found=[], scan_duration_seconds=0),
            candidates=[dep], skipped=[],
        )
        verdict = AnalysisReport(dependency_name="click", is_breaking=False, error_summary="",
                                 changelog_citation="8.0.0: Python 2 dropped", suggested_human_fix="",
                                 confidence=confidence)
        kwargs = {} if threshold is None else {"confidence_threshold": threshold}
        return assemble_report(ScanWebhookPayload(repo_url="https://x/y"), prepared, [verdict],
                               duration=1.0, tokens=TokenUsage(), **kwargs)

    def test_low_confidence_safe_verdict_is_flagged(self) -> None:
        report = self._assemble(0.4, 0.7)

        assert "low confidence" in report.reviews["click"] and "0.40" in report.reviews["click"]

    def test_confident_or_unconfigured_verdicts_are_not_flagged(self) -> None:
        assert self._assemble(0.9, 0.7).reviews == {}
        assert self._assemble(0.4, None).reviews == {}

class TestEscapedNewlines:
    """MO-89: models sometimes write the two characters \\n instead of a line break."""

    def test_escaped_newlines_become_line_breaks(self) -> None:
        from migratowl.models.schemas import AnalysisReport

        verdict = AnalysisReport(dependency_name="express", is_breaking=True, error_summary="Routes break.\\nSee below.",
                                 changelog_citation="5.0.0", confidence=0.9,
                                 suggested_human_fix="Use a connection:\\n\\nwith engine.connect() as conn:\\n    conn.execute()")
        report = TestReviewFlags._assemble(TestReviewFlags._prepared({}), [verdict])

        fix = report.reports[0].suggested_human_fix
        assert fix == "Use a connection:\n\nwith engine.connect() as conn:\n    conn.execute()"
        assert report.reports[0].error_summary == "Routes break.\nSee below."

    def test_text_with_real_line_breaks_is_left_alone(self) -> None:
        from migratowl.models.schemas import AnalysisReport

        verdict = AnalysisReport(dependency_name="express", is_breaking=True, error_summary="line one\nuse \\n here",
                                 changelog_citation="", suggested_human_fix="", confidence=0.9)
        report = TestReviewFlags._assemble(TestReviewFlags._prepared({}), [verdict])

        assert report.reports[0].error_summary == "line one\nuse \\n here"

class TestModelCallUsage:
    """MO-77: a scan records how many model calls it made, in total and by the analysis agent itself."""

    @staticmethod
    def _llm_result():
        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, LLMResult

        usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
        message = AIMessage("x", usage_metadata=usage, response_metadata={"model_name": "m"})
        return LLMResult(generations=[[ChatGeneration(message=message)]])

    def test_handler_counts_calls_and_keeps_usage(self) -> None:
        from migratowl.api.helpers import ScanUsageCallbackHandler

        handler = ScanUsageCallbackHandler()
        handler.on_llm_end(self._llm_result())
        handler.on_llm_end(self._llm_result())

        assert handler.model_calls == 2
        assert handler.usage_metadata["m"]["input_tokens"] == 20

    def test_agent_calls_are_its_ai_messages_without_the_limit_notice(self) -> None:
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        from migratowl.api.helpers import agent_model_calls

        state = {"messages": [
            HumanMessage("brief"),
            AIMessage("", tool_calls=[{"name": "task", "args": {}, "id": "t1"}]),
            ToolMessage(content="{}", tool_call_id="t1", name="task"),
            AIMessage("{}"),
            AIMessage("Model call limits exceeded: run limit (9/9)"),
        ]}

        assert agent_model_calls(state) == 2
        assert agent_model_calls({}) == 0
