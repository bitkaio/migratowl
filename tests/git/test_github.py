# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for GitHub API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from migratowl.git.github import GitHubClient, parse_github_repo


class TestParseGithubRepo:
    def test_standard_url(self) -> None:
        assert parse_github_repo("https://github.com/myorg/myrepo") == ("myorg", "myrepo")

    def test_git_suffix_stripped(self) -> None:
        assert parse_github_repo("https://github.com/myorg/myrepo.git") == ("myorg", "myrepo")

    def test_enterprise_url(self) -> None:
        assert parse_github_repo("https://github.corp.com/team/service") == ("team", "service")

    def test_trailing_slash_handled(self) -> None:
        assert parse_github_repo("https://github.com/myorg/myrepo/") == ("myorg", "myrepo")

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_github_repo("https://github.com/only-one-segment")


class TestGitHubClientPostPrComment:
    @pytest.mark.asyncio
    async def test_posts_to_correct_endpoint(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = MagicMock(status_code=201)

        with patch("migratowl.git.github.get_http_client", return_value=mock_client):
            gh = GitHubClient("test-token", "https://api.github.com")
            await gh.post_pr_comment("owner", "repo", 42, "hello")

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "repos/owner/repo/issues/42/comments" in call_kwargs.args[0]
        assert call_kwargs.kwargs["json"] == {"body": "hello"}

    @pytest.mark.asyncio
    async def test_authorization_header_sent(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = MagicMock(status_code=201)

        with patch("migratowl.git.github.get_http_client", return_value=mock_client):
            gh = GitHubClient("my-token", "https://api.github.com")
            await gh.post_pr_comment("o", "r", 1, "body")

        headers = mock_client.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer my-token"

    @pytest.mark.asyncio
    async def test_no_auth_header_when_token_empty(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = MagicMock(status_code=201)

        with patch("migratowl.git.github.get_http_client", return_value=mock_client):
            gh = GitHubClient("", "https://api.github.com")
            await gh.post_pr_comment("o", "r", 1, "body")

        headers = mock_client.post.call_args.kwargs["headers"]
        assert "Authorization" not in headers


class TestGitHubClientSetCommitStatus:
    @pytest.mark.asyncio
    async def test_posts_to_statuses_endpoint(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = MagicMock(status_code=201)

        with patch("migratowl.git.github.get_http_client", return_value=mock_client):
            gh = GitHubClient("tok", "https://api.github.com")
            await gh.set_commit_status("owner", "repo", "abc123", "pending", "scanning")

        call_args = mock_client.post.call_args
        assert "statuses/abc123" in call_args.args[0]
        assert call_args.kwargs["json"]["state"] == "pending"
        assert call_args.kwargs["json"]["context"] == "migratowl/dependency-scan"

    @pytest.mark.asyncio
    async def test_description_truncated_at_140_chars(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = MagicMock(status_code=201)
        long_desc = "x" * 200

        with patch("migratowl.git.github.get_http_client", return_value=mock_client):
            gh = GitHubClient("tok", "https://api.github.com")
            await gh.set_commit_status("o", "r", "sha", "success", long_desc)

        sent_desc = mock_client.post.call_args.kwargs["json"]["description"]
        assert len(sent_desc) == 140

    @pytest.mark.asyncio
    async def test_uses_custom_api_url(self) -> None:
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post.return_value = MagicMock(status_code=201)

        with patch("migratowl.git.github.get_http_client", return_value=mock_client):
            gh = GitHubClient("tok", "https://github.corp.com/api/v3")
            await gh.post_pr_comment("o", "r", 5, "body")

        url = mock_client.post.call_args.args[0]
        assert url.startswith("https://github.corp.com/api/v3")
