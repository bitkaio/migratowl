"""Tests for the PR notification dispatcher."""

from unittest.mock import AsyncMock, patch

import pytest

from migratowl.config import Settings
from migratowl.git.notify import notify_pr_done, notify_pr_failed, notify_pr_start
from migratowl.models.schemas import (
    AnalysisReport,
    ScanAnalysisReport,
    ScanResult,
    ScanWebhookPayload,
)


def _settings(**kwargs) -> Settings:
    return Settings(
        _env_file=None,
        github_token=kwargs.get("github_token", "gh-tok"),
        gitlab_token=kwargs.get("gitlab_token", "gl-tok"),
        github_api_url=kwargs.get("github_api_url", "https://api.github.com"),
        gitlab_api_url=kwargs.get("gitlab_api_url", "https://gitlab.com/api/v4"),
    )


def _gh_payload(**kwargs) -> ScanWebhookPayload:
    return ScanWebhookPayload(
        repo_url=kwargs.get("repo_url", "https://github.com/org/repo"),
        git_provider="github",
        pr_number=kwargs.get("pr_number", 42),
        commit_sha=kwargs.get("commit_sha", "deadbeef"),
    )


def _gl_payload(**kwargs) -> ScanWebhookPayload:
    return ScanWebhookPayload(
        repo_url=kwargs.get("repo_url", "https://gitlab.com/org/repo"),
        git_provider="gitlab",
        pr_number=kwargs.get("pr_number", 7),
        commit_sha=kwargs.get("commit_sha", "cafebabe"),
    )


def _report(breaking: int = 0) -> ScanAnalysisReport:
    reports = []
    for i in range(breaking):
        reports.append(
            AnalysisReport(
                dependency_name=f"pkg{i}",
                is_breaking=True,
                error_summary="err",
                changelog_citation="cite",
                suggested_human_fix="fix",
                confidence=0.9,
            )
        )
    return ScanAnalysisReport(
        repo_url="https://github.com/org/repo",
        branch_name="main",
        scan_result=ScanResult(
            all_deps=[], outdated=[], manifests_found=[], scan_duration_seconds=1.0
        ),
        reports=reports,
        total_duration_seconds=5.0,
    )


class TestNotifyPrStart:
    @pytest.mark.asyncio
    async def test_sets_pending_status_for_github(self) -> None:
        mock_gh = AsyncMock()
        with patch("migratowl.git.notify.GitHubClient", return_value=mock_gh):
            await notify_pr_start(_gh_payload(), _settings())
        mock_gh.set_commit_status.assert_awaited_once()
        args = mock_gh.set_commit_status.call_args
        assert args.args[3] == "pending"

    @pytest.mark.asyncio
    async def test_sets_running_status_for_gitlab(self) -> None:
        mock_gl = AsyncMock()
        with patch("migratowl.git.notify.GitLabClient", return_value=mock_gl):
            await notify_pr_start(_gl_payload(), _settings())
        mock_gl.set_commit_status.assert_awaited_once()
        args = mock_gl.set_commit_status.call_args
        assert args.args[2] == "running"

    @pytest.mark.asyncio
    async def test_no_op_when_pr_number_missing(self) -> None:
        mock_gh = AsyncMock()
        payload = ScanWebhookPayload(
            repo_url="https://github.com/o/r", commit_sha="abc"
        )
        with patch("migratowl.git.notify.GitHubClient", return_value=mock_gh):
            await notify_pr_start(payload, _settings())
        mock_gh.set_commit_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_op_when_commit_sha_missing(self) -> None:
        mock_gh = AsyncMock()
        payload = ScanWebhookPayload(
            repo_url="https://github.com/o/r", pr_number=1
        )
        with patch("migratowl.git.notify.GitHubClient", return_value=mock_gh):
            await notify_pr_start(payload, _settings())
        mock_gh.set_commit_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_swallows_exception(self) -> None:
        mock_gh = AsyncMock()
        mock_gh.set_commit_status.side_effect = RuntimeError("network error")
        with patch("migratowl.git.notify.GitHubClient", return_value=mock_gh):
            await notify_pr_start(_gh_payload(), _settings())


class TestNotifyPrDone:
    @pytest.mark.asyncio
    async def test_posts_comment_and_success_status_for_github(self) -> None:
        mock_gh = AsyncMock()
        with patch("migratowl.git.notify.GitHubClient", return_value=mock_gh):
            await notify_pr_done(_gh_payload(), _report(breaking=0), _settings())
        mock_gh.post_pr_comment.assert_awaited_once()
        mock_gh.set_commit_status.assert_awaited_once()
        status_args = mock_gh.set_commit_status.call_args.args
        assert status_args[3] == "success"

    @pytest.mark.asyncio
    async def test_posts_failure_status_when_breaking(self) -> None:
        mock_gh = AsyncMock()
        with patch("migratowl.git.notify.GitHubClient", return_value=mock_gh):
            await notify_pr_done(_gh_payload(), _report(breaking=2), _settings())
        status_args = mock_gh.set_commit_status.call_args.args
        assert status_args[3] == "failure"
        assert "2" in status_args[4]

    @pytest.mark.asyncio
    async def test_comment_body_comes_from_formatter(self) -> None:
        mock_gh = AsyncMock()
        with patch("migratowl.git.notify.GitHubClient", return_value=mock_gh), \
             patch("migratowl.git.notify.format_pr_comment", return_value="formatted-body") as mock_fmt:
            await notify_pr_done(_gh_payload(), _report(), _settings())
        mock_fmt.assert_called_once()
        body_sent = mock_gh.post_pr_comment.call_args.args[3]
        assert body_sent == "formatted-body"

    @pytest.mark.asyncio
    async def test_posts_mr_comment_for_gitlab(self) -> None:
        mock_gl = AsyncMock()
        with patch("migratowl.git.notify.GitLabClient", return_value=mock_gl):
            await notify_pr_done(_gl_payload(), _report(), _settings())
        mock_gl.post_mr_comment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_op_when_pr_number_missing(self) -> None:
        mock_gh = AsyncMock()
        payload = ScanWebhookPayload(repo_url="https://github.com/o/r")
        with patch("migratowl.git.notify.GitHubClient", return_value=mock_gh):
            await notify_pr_done(payload, _report(), _settings())
        mock_gh.post_pr_comment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_op_when_pr_number_missing_gitlab(self) -> None:
        mock_gl = AsyncMock()
        payload = ScanWebhookPayload(
            repo_url="https://gitlab.com/g/r", git_provider="gitlab"
        )
        with patch("migratowl.git.notify.GitLabClient", return_value=mock_gl):
            await notify_pr_done(payload, _report(), _settings())
        mock_gl.post_mr_comment.assert_not_awaited()
        mock_gl.set_commit_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_swallows_exception(self) -> None:
        mock_gh = AsyncMock()
        mock_gh.post_pr_comment.side_effect = RuntimeError("API error")
        with patch("migratowl.git.notify.GitHubClient", return_value=mock_gh):
            await notify_pr_done(_gh_payload(), _report(), _settings())


class TestNotifyPrFailed:
    @pytest.mark.asyncio
    async def test_sets_error_status_for_github(self) -> None:
        mock_gh = AsyncMock()
        with patch("migratowl.git.notify.GitHubClient", return_value=mock_gh):
            await notify_pr_failed(_gh_payload(), _settings())
        mock_gh.set_commit_status.assert_awaited_once()
        assert mock_gh.set_commit_status.call_args.args[3] == "error"

    @pytest.mark.asyncio
    async def test_sets_canceled_status_for_gitlab(self) -> None:
        mock_gl = AsyncMock()
        with patch("migratowl.git.notify.GitLabClient", return_value=mock_gl):
            await notify_pr_failed(_gl_payload(), _settings())
        mock_gl.set_commit_status.assert_awaited_once()
        assert mock_gl.set_commit_status.call_args.args[2] == "canceled"

    @pytest.mark.asyncio
    async def test_no_op_when_commit_sha_missing(self) -> None:
        mock_gh = AsyncMock()
        payload = ScanWebhookPayload(
            repo_url="https://github.com/o/r", pr_number=1
        )
        with patch("migratowl.git.notify.GitHubClient", return_value=mock_gh):
            await notify_pr_failed(payload, _settings())
        mock_gh.set_commit_status.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_swallows_exception(self) -> None:
        mock_gh = AsyncMock()
        mock_gh.set_commit_status.side_effect = ConnectionError("timeout")
        with patch("migratowl.git.notify.GitHubClient", return_value=mock_gh):
            await notify_pr_failed(_gh_payload(), _settings())
