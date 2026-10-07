# Copyright bitkaio LLC
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

"""Dispatch PR notifications to GitHub or GitLab after a scan.

Rules, for every outcome: a commit status whenever ``commit_sha`` is set, and a
PR/MR comment whenever ``pr_number`` is set. Each call is guarded on its own, so
a failed comment never suppresses the status (or the other way round).
"""

import logging
from collections.abc import Awaitable

from migratowl.config import Settings
from migratowl.git.formatter import format_pr_comment
from migratowl.git.github import GitHubClient, parse_github_repo
from migratowl.git.gitlab import GitLabClient
from migratowl.models.schemas import ScanAnalysisReport, ScanWebhookPayload

logger = logging.getLogger(__name__)

_FAILURE_DETAIL_CHARS = 1000


async def _guarded(what: str, payload: ScanWebhookPayload, call: Awaitable[None]) -> None:
    try:
        await call
    except Exception:
        logger.warning("Failed to post %s for %s", what, payload.repo_url, exc_info=True)


async def _comment(payload: ScanWebhookPayload, settings: Settings, body: str) -> None:
    if payload.pr_number is None:
        return
    if payload.git_provider == "github":
        owner, repo = parse_github_repo(payload.repo_url)
        gh = GitHubClient(settings.github_token, settings.github_api_url)
        await gh.post_pr_comment(owner, repo, payload.pr_number, body)
    elif payload.git_provider == "gitlab":
        gl = GitLabClient(settings.gitlab_token, settings.gitlab_api_url)
        await gl.post_mr_comment(payload.repo_url, payload.pr_number, body)


async def _status(
    payload: ScanWebhookPayload,
    settings: Settings,
    *,
    github: str,
    gitlab: str,
    description: str,
) -> None:
    if payload.commit_sha is None:
        return
    if payload.git_provider == "github":
        owner, repo = parse_github_repo(payload.repo_url)
        gh = GitHubClient(settings.github_token, settings.github_api_url)
        await gh.set_commit_status(owner, repo, payload.commit_sha, github, description)
    elif payload.git_provider == "gitlab":
        gl = GitLabClient(settings.gitlab_token, settings.gitlab_api_url)
        await gl.set_commit_status(payload.repo_url, payload.commit_sha, gitlab, description)


def format_failure_comment(error: str) -> str:
    """PR/MR comment for a scan that ended without a report."""
    detail = (error.strip() or "Internal scan error")[:_FAILURE_DETAIL_CHARS]
    detail = detail.replace("```", "` ` `")  # keep the code fence intact
    return (
        "## Migratowl Dependency Analysis\n\n"
        "Migratowl could not finish this scan, so no dependency results are available.\n\n"
        f"```\n{detail}\n```"
    )


async def notify_pr_start(payload: ScanWebhookPayload, settings: Settings) -> None:
    """Set a pending/running commit status when a scan starts."""
    await _guarded(
        "pending status",
        payload,
        _status(payload, settings, github="pending", gitlab="running",
                description="Migratowl: scanning dependencies…"),
    )


async def notify_pr_done(
    payload: ScanWebhookPayload,
    report: ScanAnalysisReport,
    settings: Settings,
) -> None:
    """Post the report as a PR/MR comment and set the final commit status."""
    breaking_count = sum(1 for r in report.reports if r.is_breaking)
    description = (
        f"Migratowl: {breaking_count} breaking upgrade(s) found"
        if breaking_count
        else "Migratowl: all upgrades safe"
    )
    await _guarded("PR comment", payload, _comment(payload, settings, format_pr_comment(report)))
    await _guarded(
        "final status",
        payload,
        _status(
            payload,
            settings,
            github="failure" if breaking_count else "success",
            gitlab="failed" if breaking_count else "success",
            description=description,
        ),
    )


async def notify_pr_failed(payload: ScanWebhookPayload, settings: Settings, *, error: str = "") -> None:
    """Comment that the scan failed and set an error/canceled commit status."""
    await _guarded("failure comment", payload, _comment(payload, settings, format_failure_comment(error)))
    await _guarded(
        "error status",
        payload,
        _status(payload, settings, github="error", gitlab="canceled", description="Migratowl: scan failed"),
    )
