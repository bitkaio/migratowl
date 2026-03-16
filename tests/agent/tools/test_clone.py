"""Tests for clone_repo tool."""

from dataclasses import dataclass
from unittest.mock import MagicMock

from migratowl.agent.tools.clone import create_clone_repo_tool

CUSTOM_WORKSPACE = "/opt/workspace"


@dataclass
class _ExecResult:
    output: str
    exit_code: int


def _make_backend(output: str = "", exit_code: int = 0) -> MagicMock:
    backend = MagicMock()
    backend.execute.return_value = _ExecResult(output=output, exit_code=exit_code)
    return backend


class TestCloneRepoTool:
    def test_successful_clone(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            _ExecResult(output="Cloning into '/home/user/workspace'...\n", exit_code=0),
            _ExecResult(output="README.md\n", exit_code=0),
        ]
        tool = create_clone_repo_tool(lambda: backend)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        assert backend.execute.call_args_list[0][0][0] == (
            "git clone --branch main --depth 1 https://github.com/psf/requests /home/user/workspace"
        )
        assert "/home/user/workspace" in result
        assert "success" in result.lower() or "cloned" in result.lower()

    def test_custom_workspace_path(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            _ExecResult(output="Cloning...\n", exit_code=0),
            _ExecResult(output="README.md\n", exit_code=0),
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=CUSTOM_WORKSPACE)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        clone_cmd = backend.execute.call_args_list[0][0][0]
        assert CUSTOM_WORKSPACE in clone_cmd
        assert CUSTOM_WORKSPACE in result

    def test_failed_clone(self) -> None:
        backend = _make_backend(
            output="fatal: repository 'https://github.com/bad/repo' not found",
            exit_code=128,
        )
        tool = create_clone_repo_tool(lambda: backend)

        result = tool.invoke({"repo_url": "https://github.com/bad/repo"})

        assert "error" in result.lower() or "failed" in result.lower()
        assert "128" in result

    def test_branch_parameter(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            _ExecResult(output="", exit_code=0),
            _ExecResult(output="README.md\n", exit_code=0),
        ]
        tool = create_clone_repo_tool(lambda: backend)

        tool.invoke({"repo_url": "https://github.com/psf/requests", "branch": "develop"})

        clone_cmd = backend.execute.call_args_list[0][0][0]
        assert clone_cmd == "git clone --branch develop --depth 1 https://github.com/psf/requests /home/user/workspace"

    def test_default_branch_is_main(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            _ExecResult(output="", exit_code=0),
            _ExecResult(output="README.md\n", exit_code=0),
        ]
        tool = create_clone_repo_tool(lambda: backend)

        tool.invoke({"repo_url": "https://github.com/psf/requests"})

        clone_cmd = backend.execute.call_args_list[0][0][0]
        assert "--branch main" in clone_cmd

    def test_verifies_files_after_clone(self) -> None:
        """After a successful git clone, the tool should verify files exist in the workspace."""
        backend = MagicMock()
        backend.execute.side_effect = [
            _ExecResult(output="Cloning into '/home/user/workspace'...\n", exit_code=0),
            _ExecResult(output="README.md\nsetup.py\nsrc/\n", exit_code=0),
        ]
        tool = create_clone_repo_tool(lambda: backend)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        assert backend.execute.call_count == 2
        verify_cmd = backend.execute.call_args_list[1][0][0]
        assert "ls" in verify_cmd
        assert "/home/user/workspace" in verify_cmd
        assert "success" in result.lower() or "cloned" in result.lower()

    def test_fails_when_workspace_empty_after_clone(self) -> None:
        """If git clone exits 0 but workspace is empty, the tool should report failure."""
        backend = MagicMock()
        backend.execute.side_effect = [
            _ExecResult(output="Cloning into '/home/user/workspace'...\n", exit_code=0),
            _ExecResult(output="", exit_code=0),  # ls returns nothing
        ]
        tool = create_clone_repo_tool(lambda: backend)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        assert "empty" in result.lower() or "failed" in result.lower() or "no files" in result.lower()
