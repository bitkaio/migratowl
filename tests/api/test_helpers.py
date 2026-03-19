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

    def test_includes_max_deps(self) -> None:
        payload = ScanWebhookPayload(
            repo_url="https://github.com/x/y", max_deps=10
        )
        msg = build_user_message(payload)
        assert "10" in msg


class TestExtractReport:
    def test_extracts_from_final_message(self) -> None:
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
                    '"reports": [], "total_duration_seconds": 2.0}',
                },
            ]
        }
        report = extract_report(agent_result, payload)
        assert report.repo_url == "https://github.com/x/y"
        assert report.total_duration_seconds == 2.0

    def test_returns_empty_report_on_missing_json(self) -> None:
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
