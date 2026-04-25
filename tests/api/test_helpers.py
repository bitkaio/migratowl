# SPDX-License-Identifier: Apache-2.0

"""Tests for webhook helper functions."""

from langchain_core.messages import AIMessage, HumanMessage

from migratowl.api.helpers import _accumulate_tokens, build_user_message, extract_report
from migratowl.models.schemas import Ecosystem, ScanWebhookPayload


class TestBuildUserMessage:
    def test_minimal_payload(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        msg = build_user_message(payload)
        assert "https://github.com/x/y" in msg
        assert "main" in msg  # default branch

    def test_includes_branch(self) -> None:
        payload = ScanWebhookPayload(
            repo_url="https://github.com/x/y", branch_name="develop"
        )
        msg = build_user_message(payload)
        assert "develop" in msg

    def test_includes_exclude_deps(self) -> None:
        payload = ScanWebhookPayload(
            repo_url="https://github.com/x/y",
            exclude_deps=["setuptools", "pip"],
        )
        msg = build_user_message(payload)
        assert "setuptools" in msg
        assert "pip" in msg

    def test_includes_ecosystems(self) -> None:
        payload = ScanWebhookPayload(
            repo_url="https://github.com/x/y",
            ecosystems=[Ecosystem.PYTHON, Ecosystem.NODEJS],
        )
        msg = build_user_message(payload)
        assert "python" in msg
        assert "nodejs" in msg

    def test_includes_check_deps(self) -> None:
        payload = ScanWebhookPayload(
            repo_url="https://github.com/x/y",
            check_deps=["requests", "flask"],
        )
        msg = build_user_message(payload)
        assert "requests" in msg
        assert "flask" in msg

    def test_check_deps_not_in_message_when_empty(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        msg = build_user_message(payload)
        assert "Only check" not in msg

    def test_includes_max_deps(self) -> None:
        payload = ScanWebhookPayload(
            repo_url="https://github.com/x/y", max_deps=10
        )
        msg = build_user_message(payload)
        assert "10" in msg


class TestExtractReport:
    def test_extracts_structured_response_pydantic_model(self) -> None:
        from migratowl.models.schemas import ScanAnalysisReport, ScanResult

        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        structured = ScanAnalysisReport(
            repo_url="https://github.com/x/y",
            branch_name="main",
            scan_result=ScanResult(
                all_deps=[], outdated=[], manifests_found=[], scan_duration_seconds=1.0
            ),
            reports=[],
            total_duration_seconds=5.0,
        )
        agent_result = {"structured_response": structured, "messages": []}
        report = extract_report(agent_result, payload)
        assert report.repo_url == "https://github.com/x/y"
        assert report.total_duration_seconds == 5.0

    def test_extracts_structured_response_dict(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        agent_result = {
            "structured_response": {
                "repo_url": "https://github.com/x/y",
                "branch_name": "main",
                "scan_result": {
                    "all_deps": [],
                    "outdated": [],
                    "manifests_found": [],
                    "scan_duration_seconds": 1.0,
                },
                "reports": [],
                "total_duration_seconds": 3.0,
            },
            "messages": [],
        }
        report = extract_report(agent_result, payload)
        assert report.repo_url == "https://github.com/x/y"
        assert report.total_duration_seconds == 3.0

    def test_falls_back_to_json_in_message_dict(self) -> None:
        """When structured_response is missing, parse JSON from message dicts."""
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        agent_result = {
            "messages": [
                {"role": "assistant", "content": "Working on it..."},
                {
                    "role": "assistant",
                    "content": '{"repo_url": "https://github.com/x/y", '
                    '"branch_name": "main", '
                    '"scan_result": {"all_deps": [], "outdated": [], '
                    '"manifests_found": [], "scan_duration_seconds": 1.0}, '
                    '"reports": [], "total_duration_seconds": 7.0}',
                },
            ]
        }
        report = extract_report(agent_result, payload)
        assert report.total_duration_seconds == 7.0

    def test_falls_back_to_json_in_langchain_message(self) -> None:
        """When structured_response is missing, parse JSON from LangChain AIMessage."""
        from langchain_core.messages import AIMessage

        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        agent_result = {
            "messages": [
                AIMessage(content="Working on it..."),
                AIMessage(
                    content='{"repo_url": "https://github.com/x/y", '
                    '"branch_name": "main", '
                    '"scan_result": {"all_deps": [], "outdated": [], '
                    '"manifests_found": [], "scan_duration_seconds": 1.0}, '
                    '"reports": [], "total_duration_seconds": 9.0}'
                ),
            ]
        }
        report = extract_report(agent_result, payload)
        assert report.total_duration_seconds == 9.0

    def test_returns_empty_report_when_no_parseable_content(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        agent_result = {
            "messages": [
                {"role": "assistant", "content": "I couldn't complete the analysis."},
            ]
        }
        report = extract_report(agent_result, payload)
        assert report.repo_url == "https://github.com/x/y"
        assert report.reports == []

    def test_returns_empty_report_on_no_messages(self) -> None:
        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        agent_result = {"messages": []}
        report = extract_report(agent_result, payload)
        assert report.repo_url == "https://github.com/x/y"

    def test_populates_token_counts_from_ai_messages(self) -> None:
        from migratowl.models.schemas import ScanAnalysisReport, ScanResult

        payload = ScanWebhookPayload(repo_url="https://github.com/x/y")
        structured = ScanAnalysisReport(
            repo_url="https://github.com/x/y",
            branch_name="main",
            scan_result=ScanResult(
                all_deps=[], outdated=[], manifests_found=[], scan_duration_seconds=1.0
            ),
            reports=[],
            total_duration_seconds=5.0,
        )
        agent_result = {
            "structured_response": structured,
            "messages": [
                AIMessage(
                    content="done",
                    usage_metadata={"input_tokens": 1000, "output_tokens": 400, "total_tokens": 1400},
                )
            ],
        }
        report = extract_report(agent_result, payload)
        assert report.total_input_tokens == 1000
        assert report.total_output_tokens == 400


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