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

"""Clone repository and copy source tools for the Migratowl agent."""

import base64
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from langchain.tools import tool

from migratowl.agent.tools.update import q
from migratowl.config import Settings

# host -> (username, token); the username is what GitHub / GitLab expect for a token over HTTPS.
CloneTokens = Mapping[str, tuple[str, str]]


def clone_tokens(settings: Settings) -> dict[str, tuple[str, str]]:
    """Clone credentials per git host, from the GitHub / GitLab tokens already configured."""
    tokens: dict[str, tuple[str, str]] = {}
    if settings.github_token:
        host = (urlsplit(settings.github_api_url).hostname or "github.com").removeprefix("api.")
        tokens[host] = ("x-access-token", settings.github_token)
    if settings.gitlab_token:
        tokens[urlsplit(settings.gitlab_api_url).hostname or "gitlab.com"] = (
            settings.gitlab_token_user,
            settings.gitlab_token,
        )
    return tokens


def _clone_auth(repo_url: str, tokens: CloneTokens) -> tuple[str, list[str], tuple[str, ...]]:
    """Split ``repo_url`` into (clean URL, ``git -c`` args, secrets to scrub from output).

    The credential travels as a per-host ``http.<url>.extraHeader`` on this one command. It is never
    in the URL or in ``.git/config``, where code the sandbox later runs could read it, and git's
    error messages never echo it.
    """
    parts = urlsplit(repo_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return repo_url, [], ()
    credentials: tuple[str, str] | None = None
    if parts.username or parts.password:
        credentials = (unquote(parts.username or ""), unquote(parts.password or ""))
    elif parts.scheme == "https" and parts.hostname in tokens:  # a configured token never travels in cleartext
        credentials = tokens[parts.hostname]
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    clean = urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    if credentials is None:
        return clean, [], ()
    user, secret = credentials
    header = base64.b64encode(f"{user}:{secret}".encode()).decode()
    arg = f"http.{parts.scheme}://{host}/.extraHeader=Authorization: Basic {header}"
    return clean, ["-c", arg], tuple(x for x in (user, secret, header) if x)


def create_clone_repo_tool(
    get_backend: Callable[[], Any],
    workspace_path: str,
    tokens: CloneTokens | None = None,
) -> Any:
    """Create a clone_repo tool bound to a sandbox backend.

    Args:
        get_backend: Callable that returns a sandbox backend with an
            ``execute()`` method (e.g. a K8s sandbox).
        workspace_path: Root workspace path inside the sandbox.
        tokens: Clone credentials per git host (see ``clone_tokens``), for private repositories.
    """
    source_path = f"{workspace_path}/source"

    @tool
    def clone_repo(repo_url: str, branch: str = "main") -> str:
        """Clone a Git repository into the sandbox workspace.

        Clones into {workspace}/source/. If source/ already has files, skips
        the clone and returns immediately.

        Args:
            repo_url: HTTPS URL of the repository to clone.
            branch: Branch to clone (default: "main").
        """
        backend = get_backend()

        # Check if source/ already has files
        check = backend.execute(f"ls {q(source_path)}")
        if check.exit_code == 0 and check.output.strip():
            return f"source already present at {source_path} — skipping clone"

        repo_url, git_auth, secrets = _clone_auth(repo_url, tokens or {})
        auth = "".join(f" {q(a)}" for a in git_auth)

        def scrub(text: str) -> str:
            for secret in secrets:
                text = text.replace(secret, "***")
            return text

        def attempt(auth_args: str) -> tuple[Any, str, str]:
            """Clone with ``auth_args``; returns (result, branch used, failure message when it failed)."""
            cmd = f"git{auth_args} clone --branch {q(branch)} --depth 1 -- {q(repo_url)} {q(source_path)}"
            result = backend.execute(cmd)
            if result.exit_code == 0:
                return result, branch, ""
            if branch != "main":
                return result, branch, (
                    f"Failed to clone {repo_url} (exit code {result.exit_code}): {scrub(result.output)}"
                )
            # Fallback: retry with the repo's default branch
            backend.execute(f"rm -rf {q(source_path)}")
            result_default = backend.execute(f"git{auth_args} clone --depth 1 -- {q(repo_url)} {q(source_path)}")
            if result_default.exit_code == 0:
                return result_default, "(default)", ""
            return result_default, branch, (
                f"Failed to clone {repo_url}: branch 'main' failed (exit {result.exit_code}), "
                f"default branch also failed (exit {result_default.exit_code}): {scrub(result_default.output)}"
            )

        result, branch, failure = attempt(auth)
        if failure and auth:
            # A token the host rejects (a CI job token, an expired PAT) must not break a public repo:
            # retry without it. A private repo fails again, and the first error is the one reported.
            backend.execute(f"rm -rf {q(source_path)}")
            result_anonymous, branch_anonymous, failure_anonymous = attempt("")
            if not failure_anonymous:
                result, branch, failure = result_anonymous, branch_anonymous, ""
        if failure:
            return failure

        verify = backend.execute(f"ls {q(source_path)}")
        if not verify.output.strip():
            return (
                f"Failed to clone {repo_url}: workspace is empty after clone "
                f"(no files found in {source_path})"
            )

        return f"Successfully cloned {repo_url} (branch: {branch}) to {source_path}\n{scrub(result.output)}"

    return clone_repo


def create_copy_source_tool(
    get_backend: Callable[[], Any],
    workspace_path: str,
) -> Any:
    """Create a copy_source tool bound to a sandbox backend.

    Args:
        get_backend: Callable that returns a sandbox backend.
        workspace_path: Root workspace path inside the sandbox.
    """
    source_path = f"{workspace_path}/source"

    @tool
    def copy_source(folder_name: str) -> str:
        """Copy the immutable source/ directory to a new working folder.

        Args:
            folder_name: Name of the target folder (e.g. "main", "requests").
        """
        backend = get_backend()
        target_path = f"{workspace_path}/{folder_name}"

        # Verify source/ exists and is non-empty
        check = backend.execute(f"ls {q(source_path)}")
        if check.exit_code != 0:
            return f"source does not exist at {source_path} — clone the repository first"
        if not check.output.strip():
            return f"source is empty at {source_path} — no files to copy"

        # Create target and copy
        backend.execute(f"mkdir -p {q(target_path)}")
        cp_result = backend.execute(f"cp -a {q(source_path + '/.')} {q(target_path + '/')}")
        if cp_result.exit_code != 0:
            return f"Failed to copy source to {target_path} (exit code {cp_result.exit_code}): {cp_result.output}"

        # Verify target has files
        verify = backend.execute(f"ls {q(target_path)}")
        if not verify.output.strip():
            return f"Copy failed: {target_path} is empty after copy"

        return f"Successfully copied source to {target_path}"

    return copy_source