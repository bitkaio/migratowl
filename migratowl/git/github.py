# Copyright 2024 bitkaio LLC
#
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

"""GitHub REST API client for PR comments and commit statuses."""

from urllib.parse import urlparse

from migratowl.http import get_http_client


def parse_github_repo(repo_url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub HTTPS URL.

    Works for github.com and GitHub Enterprise Server URLs.
    """
    cleaned = repo_url.rstrip("/").removesuffix(".git")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Cannot parse GitHub repo URL: {repo_url!r}")
    path_parts = [p for p in parsed.path.split("/") if p]
    if len(path_parts) < 2:
        raise ValueError(f"Cannot parse GitHub repo URL: {repo_url!r}")
    return path_parts[-2], path_parts[-1]


class GitHubClient:
    """Thin async wrapper around the GitHub REST API."""

    def __init__(self, token: str, api_url: str = "https://api.github.com") -> None:
        self._token = token
        self._api_url = api_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def post_pr_comment(
        self, owner: str, repo: str, pr_number: int, body: str
    ) -> None:
        """Post a general comment on a pull request."""
        client = get_http_client()
        url = f"{self._api_url}/repos/{owner}/{repo}/issues/{pr_number}/comments"
        response = await client.post(url, json={"body": body}, headers=self._headers())
        response.raise_for_status()

    async def set_commit_status(
        self,
        owner: str,
        repo: str,
        sha: str,
        state: str,
        description: str,
        context: str = "migratowl/dependency-scan",
    ) -> None:
        """Set a commit status (pending / success / failure / error)."""
        client = get_http_client()
        url = f"{self._api_url}/repos/{owner}/{repo}/statuses/{sha}"
        response = await client.post(
            url,
            json={
                "state": state,
                "description": description[:140],
                "context": context,
            },
            headers=self._headers(),
        )
        response.raise_for_status()