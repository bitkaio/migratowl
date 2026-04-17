"""Tests for webhook helper functions."""

from migratowl.api.helpers import build_user_message, extract_report
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
