"""Tests for clone_repo and copy_source tools."""

from unittest.mock import MagicMock

from migratowl.agent.tools.clone import create_clone_repo_tool, create_copy_source_tool
from tests.conftest import ExecResult, make_backend

DEFAULT_WORKSPACE = "/home/user/workspace"
CUSTOM_WORKSPACE = "/opt/workspace"


class TestCloneRepoTool:
    """clone_repo now: ls source/ → (skip if populated) → git clone → ls verify."""

    def test_successful_clone_into_source(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # ls source/ — empty
            ExecResult(output="Cloning into 'source'...\n", exit_code=0),  # git clone
            ExecResult(output="README.md\n", exit_code=0),  # ls verify
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        clone_cmd = backend.execute.call_args_list[1][0][0]
        assert f"{DEFAULT_WORKSPACE}/source" in clone_cmd
        assert "success" in result.lower() or "cloned" in result.lower()

    def test_custom_workspace_path(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # ls source/ — empty
            ExecResult(output="Cloning...\n", exit_code=0),
            ExecResult(output="README.md\n", exit_code=0),
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=CUSTOM_WORKSPACE)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        clone_cmd = backend.execute.call_args_list[1][0][0]
        assert f"{CUSTOM_WORKSPACE}/source" in clone_cmd
        assert CUSTOM_WORKSPACE in result

    def test_failed_clone(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # ls source/ — empty
            ExecResult(
                output="fatal: repository 'https://github.com/bad/repo' not found",
                exit_code=128,
            ),
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"repo_url": "https://github.com/bad/repo"})

        assert "error" in result.lower() or "failed" in result.lower()
        assert "128" in result

    def test_branch_parameter(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # ls source/ — empty
            ExecResult(output="", exit_code=0),  # git clone
            ExecResult(output="README.md\n", exit_code=0),  # ls verify
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        tool.invoke({"repo_url": "https://github.com/psf/requests", "branch": "develop"})

        clone_cmd = backend.execute.call_args_list[1][0][0]
        assert clone_cmd == (
            "git clone --branch develop --depth 1 "
            "https://github.com/psf/requests /home/user/workspace/source"
        )

    def test_default_branch_is_main(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=0),
            ExecResult(output="README.md\n", exit_code=0),
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        tool.invoke({"repo_url": "https://github.com/psf/requests"})

        clone_cmd = backend.execute.call_args_list[1][0][0]
        assert "--branch main" in clone_cmd

    def test_verifies_files_after_clone(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # ls source/ — empty
            ExecResult(output="Cloning...\n", exit_code=0),
            ExecResult(output="README.md\nsetup.py\nsrc/\n", exit_code=0),
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        assert backend.execute.call_count == 3
        verify_cmd = backend.execute.call_args_list[2][0][0]
        assert "ls" in verify_cmd
        assert f"{DEFAULT_WORKSPACE}/source" in verify_cmd
        assert "success" in result.lower() or "cloned" in result.lower()

    def test_fails_when_workspace_empty_after_clone(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # ls source/ — empty
            ExecResult(output="Cloning...\n", exit_code=0),
            ExecResult(output="", exit_code=0),  # ls verify — empty
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        assert "empty" in result.lower() or "failed" in result.lower() or "no files" in result.lower()

    def test_skip_when_source_already_populated(self) -> None:
        """If source/ already has files, skip clone and return early."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="README.md\nsetup.py\n", exit_code=0),  # ls source/
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        assert backend.execute.call_count == 1  # only the ls check, no clone
        assert "already" in result.lower() or "present" in result.lower()

    def test_proceeds_when_source_empty(self) -> None:
        """If source/ ls returns empty, proceed with clone."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # ls source/ — empty
            ExecResult(output="Cloning...\n", exit_code=0),  # git clone
            ExecResult(output="README.md\n", exit_code=0),  # ls verify
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        assert backend.execute.call_count == 3
        assert "success" in result.lower() or "cloned" in result.lower()

    def test_proceeds_when_source_dir_missing(self) -> None:
        """If source/ doesn't exist (ls fails), proceed with clone."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="No such file or directory", exit_code=2),  # ls fails
            ExecResult(output="Cloning...\n", exit_code=0),  # git clone
            ExecResult(output="README.md\n", exit_code=0),  # ls verify
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        assert backend.execute.call_count == 3
        assert "success" in result.lower() or "cloned" in result.lower()


class TestCopySourceTool:
    def test_successful_copy(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="README.md\nsetup.py\n", exit_code=0),  # ls source/
            ExecResult(output="", exit_code=0),  # mkdir -p
            ExecResult(output="", exit_code=0),  # cp -a
            ExecResult(output="README.md\nsetup.py\n", exit_code=0),  # ls target verify
        ]
        tool = create_copy_source_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"folder_name": "main"})

        assert "success" in result.lower() or "copied" in result.lower()
        mkdir_cmd = backend.execute.call_args_list[1][0][0]
        assert "mkdir -p" in mkdir_cmd
        assert f"{DEFAULT_WORKSPACE}/main" in mkdir_cmd
        cp_cmd = backend.execute.call_args_list[2][0][0]
        assert "cp -a" in cp_cmd
        assert f"{DEFAULT_WORKSPACE}/source/." in cp_cmd
        assert f"{DEFAULT_WORKSPACE}/main/" in cp_cmd

    def test_source_missing(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="No such file or directory", exit_code=2),
        ]
        tool = create_copy_source_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"folder_name": "main"})

        assert "source" in result.lower()
        assert "not" in result.lower() or "missing" in result.lower() or "does not exist" in result.lower()

    def test_source_empty(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
        ]
        tool = create_copy_source_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"folder_name": "main"})

        assert "empty" in result.lower() or "no files" in result.lower()

    def test_target_verification_after_copy(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="README.md\n", exit_code=0),
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=0),
            ExecResult(output="README.md\n", exit_code=0),
        ]
        tool = create_copy_source_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"folder_name": "requests"})

        verify_cmd = backend.execute.call_args_list[3][0][0]
        assert "ls" in verify_cmd
        assert f"{DEFAULT_WORKSPACE}/requests" in verify_cmd
        assert "success" in result.lower() or "copied" in result.lower()

    def test_copy_fails(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="README.md\n", exit_code=0),
            ExecResult(output="", exit_code=0),
            ExecResult(output="cp: error", exit_code=1),
        ]
        tool = create_copy_source_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"folder_name": "main"})

        assert "failed" in result.lower() or "error" in result.lower()

    def test_target_empty_after_copy(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="README.md\n", exit_code=0),
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=0),  # ls target — empty
        ]
        tool = create_copy_source_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"folder_name": "main"})

        assert "empty" in result.lower() or "failed" in result.lower() or "no files" in result.lower()
