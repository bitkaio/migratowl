"""Tests for GitLab API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from migratowl.git.gitlab import GitLabClient, parse_gitlab_project


class TestParseGitlabProject:
    def test_standard_url(self) -> None:
        result = parse_gitlab_project("https://gitlab.com/mygroup/myrepo")
        assert result == "mygroup%2Fmyrepo"

    def test_nested_namespace(self) -> None:
        result = parse_gitlab_project("https://gitlab.com/group/subgroup/repo")
        assert result == "group%2Fsubgroup%2Frepo"

    def test_git_suffix_stripped(self) -> None:
        result = parse_gitlab_project("https://gitlab.com/group/repo.git")
        assert result == "group%2Frepo"

    def test_self_hosted_url(self) -> None:
        result = parse_gitlab_project("https://gitlab.internal.com/team/service")
        assert result == "team%2Fservice"

    def test_trailing_slash_handled(self) -> None:
        result = parse_gitlab_project("https://gitlab.com/group/repo/")
        assert result == "group%2Frepo"


class TestGitLabClientPostMrComment:
    @pytest.mark.asyncio
    async def test_posts_to_notes_endpoint(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = MagicMock(status_code=201)

        with patch("migratowl.git.gitlab.get_http_client", return_value=mock_client):
            gl = GitLabClient("glpat-test", "https://gitlab.com/api/v4")
            await gl.post_mr_comment("https://gitlab.com/group/repo", 7, "comment body")

        call_args = mock_client.post.call_args
        assert "merge_requests/7/notes" in call_args.args[0]
        assert "group%2Frepo" in call_args.args[0]
        assert call_args.kwargs["json"] == {"body": "comment body"}

    @pytest.mark.asyncio
    async def test_private_token_header_sent(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = MagicMock(status_code=201)

        with patch("migratowl.git.gitlab.get_http_client", return_value=mock_client):
            gl = GitLabClient("glpat-mytoken", "https://gitlab.com/api/v4")
            await gl.post_mr_comment("https://gitlab.com/g/r", 1, "body")

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["PRIVATE-TOKEN"] == "glpat-mytoken"

    @pytest.mark.asyncio
    async def test_no_auth_header_when_token_empty(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = MagicMock(status_code=201)

        with patch("migratowl.git.gitlab.get_http_client", return_value=mock_client):
            gl = GitLabClient("", "https://gitlab.com/api/v4")
            await gl.post_mr_comment("https://gitlab.com/g/r", 1, "body")

        headers = mock_client.post.call_args.kwargs["headers"]
        assert "PRIVATE-TOKEN" not in headers


class TestGitLabClientSetCommitStatus:
    @pytest.mark.asyncio
    async def test_posts_to_statuses_endpoint(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = MagicMock(status_code=201)

        with patch("migratowl.git.gitlab.get_http_client", return_value=mock_client):
            gl = GitLabClient("tok", "https://gitlab.com/api/v4")
            await gl.set_commit_status(
                "https://gitlab.com/group/repo", "deadbeef", "running", "scanning"
            )

        call_args = mock_client.post.call_args
        assert "statuses/deadbeef" in call_args.args[0]
        assert call_args.kwargs["json"]["state"] == "running"
        assert call_args.kwargs["json"]["name"] == "migratowl"

    @pytest.mark.asyncio
    async def test_uses_self_hosted_api_url(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = MagicMock(status_code=201)

        with patch("migratowl.git.gitlab.get_http_client", return_value=mock_client):
            gl = GitLabClient("tok", "https://gitlab.corp.com/api/v4")
            await gl.post_mr_comment("https://gitlab.corp.com/team/svc", 3, "hi")

        url = mock_client.post.call_args.args[0]
        assert url.startswith("https://gitlab.corp.com/api/v4")
