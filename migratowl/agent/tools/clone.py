"""Clone repository tool for the MigratOwl agent."""

from collections.abc import Callable
from typing import Any

from langchain.tools import tool

DEFAULT_WORKSPACE_PATH = "/home/user/workspace"


def create_clone_repo_tool(
    get_backend: Callable[[], Any],
    workspace_path: str = DEFAULT_WORKSPACE_PATH,
) -> Any:
    """Create a clone_repo tool bound to a sandbox backend.

    Args:
        get_backend: Callable that returns a sandbox backend with an
            ``execute()`` method (e.g. a K8s sandbox).
        workspace_path: Path inside the sandbox to clone into.
    """

    @tool
    def clone_repo(repo_url: str, branch: str = "main") -> str:
        """Clone a public Git repository into the sandbox workspace.

        Args:
            repo_url: HTTPS URL of the repository to clone.
            branch: Branch to clone (default: "main").
        """
        backend = get_backend()
        cmd = f"git clone --branch {branch} --depth 1 {repo_url} {workspace_path}"
        result = backend.execute(cmd)

        if result.exit_code != 0:
            return f"Failed to clone {repo_url} (exit code {result.exit_code}): {result.output}"

        verify = backend.execute(f"ls {workspace_path}")
        if not verify.output.strip():
            return f"Failed to clone {repo_url}: workspace is empty after clone (no files found in {workspace_path})"

        return f"Successfully cloned {repo_url} (branch: {branch}) to {workspace_path}\n{result.output}"

    return clone_repo
