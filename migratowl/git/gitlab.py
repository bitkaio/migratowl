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

"""GitLab REST API client for MR comments and commit statuses."""

import re
from urllib.parse import quote

from migratowl.http import get_http_client


def parse_gitlab_project(repo_url: str) -> str:
    """Extract URL-encoded project path from a GitLab HTTPS URL.

    e.g. https://gitlab.com/group/subgroup/repo → "group%2Fsubgroup%2Frepo"
    Works for gitlab.com and self-hosted instances.
    """
    cleaned = repo_url.rstrip("/").removesuffix(".git")
    # Strip scheme + host, keep only the path after the first "/"
    path = re.sub(r"^https?://[^/]+/", "", cleaned)
    return quote(path, safe="")


class GitLabClient:
    """Thin async wrapper around the GitLab REST API v4."""

    def __init__(self, token: str, api_url: str = "https://gitlab.com/api/v4") -> None:
        self._token = token
        self._api_url = api_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            h["PRIVATE-TOKEN"] = self._token
        return h

    async def post_mr_comment(
        self, repo_url: str, mr_iid: int, body: str
    ) -> None:
        """Post a note (comment) on a merge request."""
        project = parse_gitlab_project(repo_url)
        client = get_http_client()
        url = f"{self._api_url}/projects/{project}/merge_requests/{mr_iid}/notes"
        response = await client.post(url, json={"body": body}, headers=self._headers())
        response.raise_for_status()

    async def set_commit_status(
        self,
        repo_url: str,
        sha: str,
        state: str,
        description: str,
        name: str = "migratowl",
    ) -> None:
        """Set a commit status (pending / running / success / failed / canceled)."""
        project = parse_gitlab_project(repo_url)
        client = get_http_client()
        url = f"{self._api_url}/projects/{project}/statuses/{sha}"
        response = await client.post(
            url,
            json={"state": state, "description": description, "name": name},
            headers=self._headers(),
        )
        response.raise_for_status()