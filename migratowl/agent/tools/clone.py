# SPDX-License-Identifier: Apache-2.0

"""Clone repository and copy source tools for the Migratowl agent."""

from collections.abc import Callable
from typing import Any

from langchain.tools import tool


def create_clone_repo_tool(
    get_backend: Callable[[], Any],
    workspace_path: str,
) -> Any:
    """Create a clone_repo tool bound to a sandbox backend.

    Args:
        get_backend: Callable that returns a sandbox backend with an
            ``execute()`` method (e.g. a K8s sandbox).
        workspace_path: Root workspace path inside the sandbox.
    """
    source_path = f"{workspace_path}/source"

    @tool
    def clone_repo(repo_url: str, branch: str = "main") -> str:
        """Clone a public Git repository into the sandbox workspace.

        Clones into {workspace}/source/. If source/ already has files, skips
        the clone and returns immediately.

        Args:
            repo_url: HTTPS URL of the repository to clone.
            branch: Branch to clone (default: "main").
        """
        backend = get_backend()

        # Check if source/ already has files
        check = backend.execute(f"ls {source_path}")
        if check.exit_code == 0 and check.output.strip():
            return f"source already present at {source_path} — skipping clone"

        # Clone into source/
        cmd = f"git clone --branch {branch} --depth 1 {repo_url} {source_path}"
        result = backend.execute(cmd)

        if result.exit_code != 0:
            if branch == "main":
                # Fallback: retry with repo's default branch
                backend.execute(f"rm -rf {source_path}")
                cmd_default = f"git clone --depth 1 {repo_url} {source_path}"
                result_default = backend.execute(cmd_default)
                if result_default.exit_code == 0:
                    result = result_default
                    branch = "(default)"
                else:
                    return (
                        f"Failed to clone {repo_url}: branch 'main' failed (exit {result.exit_code}), "
                        f"default branch also failed (exit {result_default.exit_code}): {result_default.output}"
                    )
            else:
                return f"Failed to clone {repo_url} (exit code {result.exit_code}): {result.output}"

        verify = backend.execute(f"ls {source_path}")
        if not verify.output.strip():
            return (
                f"Failed to clone {repo_url}: workspace is empty after clone "
                f"(no files found in {source_path})"
            )

        return f"Successfully cloned {repo_url} (branch: {branch}) to {source_path}\n{result.output}"

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
        check = backend.execute(f"ls {source_path}")
        if check.exit_code != 0:
            return f"source does not exist at {source_path} — clone the repository first"
        if not check.output.strip():
            return f"source is empty at {source_path} — no files to copy"

        # Create target and copy
        backend.execute(f"mkdir -p {target_path}")
        cp_result = backend.execute(f"cp -a {source_path}/. {target_path}/")
        if cp_result.exit_code != 0:
            return f"Failed to copy source to {target_path} (exit code {cp_result.exit_code}): {cp_result.output}"

        # Verify target has files
        verify = backend.execute(f"ls {target_path}")
        if not verify.output.strip():
            return f"Copy failed: {target_path} is empty after copy"

        return f"Successfully copied source to {target_path}"

    return copy_source