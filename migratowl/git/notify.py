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

"""Dispatch PR notifications to GitHub or GitLab after a scan."""

import logging

from migratowl.config import Settings
from migratowl.git.formatter import format_pr_comment
from migratowl.git.github import GitHubClient, parse_github_repo
from migratowl.git.gitlab import GitLabClient
from migratowl.models.schemas import ScanAnalysisReport, ScanWebhookPayload

logger = logging.getLogger(__name__)


async def notify_pr_start(payload: ScanWebhookPayload, settings: Settings) -> None:
    """Post a 'pending/running' commit status when a scan starts."""
    if payload.pr_number is None or payload.commit_sha is None:
        return
    try:
        if payload.git_provider == "github":
            owner, repo = parse_github_repo(payload.repo_url)
            gh = GitHubClient(settings.github_token, settings.github_api_url)
            await gh.set_commit_status(
                owner, repo, payload.commit_sha, "pending", "Migratowl: scanning dependencies…"
            )
        elif payload.git_provider == "gitlab":
            gl = GitLabClient(settings.gitlab_token, settings.gitlab_api_url)
            await gl.set_commit_status(
                payload.repo_url, payload.commit_sha, "running", "Migratowl: scanning dependencies…"
            )
    except Exception:
        logger.warning("Failed to post pending status for %s", payload.repo_url, exc_info=True)


async def notify_pr_done(
    payload: ScanWebhookPayload,
    report: ScanAnalysisReport,
    settings: Settings,
) -> None:
    """Post a PR comment and set the final commit status after a scan completes."""
    if payload.pr_number is None:
        return
    try:
        breaking_count = sum(1 for r in report.reports if r.is_breaking)
        comment_body = format_pr_comment(report)
        if payload.git_provider == "github":
            owner, repo = parse_github_repo(payload.repo_url)
            gh = GitHubClient(settings.github_token, settings.github_api_url)
            await gh.post_pr_comment(owner, repo, payload.pr_number, comment_body)
            if payload.commit_sha:
                state = "failure" if breaking_count > 0 else "success"
                desc = (
                    f"Migratowl: {breaking_count} breaking upgrade(s) found"
                    if breaking_count
                    else "Migratowl: all upgrades safe"
                )
                await gh.set_commit_status(owner, repo, payload.commit_sha, state, desc)
        elif payload.git_provider == "gitlab":
            gl = GitLabClient(settings.gitlab_token, settings.gitlab_api_url)
            await gl.post_mr_comment(payload.repo_url, payload.pr_number, comment_body)
            if payload.commit_sha:
                state = "failed" if breaking_count > 0 else "success"
                desc = (
                    f"Migratowl: {breaking_count} breaking upgrade(s) found"
                    if breaking_count
                    else "Migratowl: all upgrades safe"
                )
                await gl.set_commit_status(payload.repo_url, payload.commit_sha, state, desc)
    except Exception:
        logger.warning(
            "Failed to post PR notification for %s#%s",
            payload.repo_url,
            payload.pr_number,
            exc_info=True,
        )


async def notify_pr_failed(payload: ScanWebhookPayload, settings: Settings) -> None:
    """Set an error/canceled commit status when a scan fails."""
    if payload.commit_sha is None:
        return
    try:
        if payload.git_provider == "github":
            owner, repo = parse_github_repo(payload.repo_url)
            gh = GitHubClient(settings.github_token, settings.github_api_url)
            await gh.set_commit_status(
                owner, repo, payload.commit_sha, "error", "Migratowl: scan failed"
            )
        elif payload.git_provider == "gitlab":
            gl = GitLabClient(settings.gitlab_token, settings.gitlab_api_url)
            await gl.set_commit_status(
                payload.repo_url, payload.commit_sha, "canceled", "Migratowl: scan failed"
            )
    except Exception:
        logger.warning("Failed to post error status for %s", payload.repo_url, exc_info=True)