"""Tests for update_dependencies tool."""

import json
from unittest.mock import MagicMock

from migratowl.agent.tools.update import create_update_dependencies_tool
from tests.conftest import ExecResult

DEFAULT_WORKSPACE = "/home/user/workspace"


class TestUpdateDependenciesTool:
    def test_python_single_package(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # pip install requests==2.31.0
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "requests", "latest_version": "2.31.0"}])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        pip_cmd = backend.execute.call_args_list[0][0][0]
        assert "pip install requests==2.31.0" in pip_cmd
        assert f"{DEFAULT_WORKSPACE}/main" in pip_cmd
        assert "success" in result.lower() or "updated" in result.lower()

    def test_python_multiple_packages(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # pip install requests==2.31.0
            ExecResult(output="", exit_code=0),  # pip install flask==3.0.0
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([
            {"name": "requests", "latest_version": "2.31.0"},
            {"name": "flask", "latest_version": "3.0.0"},
        ])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        assert backend.execute.call_count == 2
        assert "requests" in result and "flask" in result

    def test_nodejs_packages(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="added 1 package\n", exit_code=0),  # npm install express@5.0.0
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "express", "latest_version": "5.0.0"}])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "nodejs",
            "packages_json": packages,
        })

        npm_cmd = backend.execute.call_args_list[0][0][0]
        assert "npm install express@5.0.0" in npm_cmd
        assert f"{DEFAULT_WORKSPACE}/main" in npm_cmd

    def test_go_packages(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # go get
            ExecResult(output="", exit_code=0),  # go mod tidy
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "github.com/gin-gonic/gin", "latest_version": "1.9.1"}])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "go",
            "packages_json": packages,
        })

        go_cmd = backend.execute.call_args_list[0][0][0]
        assert "go get github.com/gin-gonic/gin@v1.9.1" in go_cmd
        tidy_cmd = backend.execute.call_args_list[1][0][0]
        assert "go mod tidy" in tidy_cmd

    def test_rust_packages(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # cargo update
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "serde", "latest_version": "1.0.200"}])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "rust",
            "packages_json": packages,
        })

        cargo_cmd = backend.execute.call_args_list[0][0][0]
        assert "cargo update -p serde --precise 1.0.200" in cargo_cmd

    def test_failed_update(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="ERROR: No matching distribution found\n", exit_code=1),
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "nonexistent", "latest_version": "1.0.0"}])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        assert "fail" in result.lower() or "error" in result.lower()

    def test_commands_wrapped_in_shell(self) -> None:
        """Commands must be wrapped in sh -c since sandbox execute doesn't use a shell."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # pip install
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "requests", "latest_version": "2.31.0"}])
        tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        for call in backend.execute.call_args_list:
            cmd = call[0][0]
            assert cmd.startswith("sh -c "), f"Command not shell-wrapped: {cmd}"

    def test_python_sets_break_system_packages(self) -> None:
        """Python pip commands must set PIP_BREAK_SYSTEM_PACKAGES=1 for PEP 668 containers."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "requests", "latest_version": "2.31.0"}])
        tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        for call in backend.execute.call_args_list:
            cmd = call[0][0]
            assert "PIP_BREAK_SYSTEM_PACKAGES=1" in cmd, f"Missing PIP env var: {cmd}"

    def test_empty_package_list(self) -> None:
        backend = MagicMock()
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        backend.execute.assert_not_called()
        assert "0" in result or "no packages" in result.lower() or "success" in result.lower()

    def test_does_not_run_install_command_after_python_update(self) -> None:
        """update_dependencies must NOT run install_command — that belongs to execute_project."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # pip install pkg==ver only
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "requests", "latest_version": "2.31.0"}])
        tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        assert backend.execute.call_count == 1
        cmd = backend.execute.call_args_list[0][0][0]
        assert "pip install -e" not in cmd  # no install_command re-run
        assert "requests==2.31.0" in cmd

    def test_does_not_run_install_command_after_nodejs_update(self) -> None:
        """update_dependencies must NOT run install_command — that belongs to execute_project."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # npm install pkg@ver only
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "express", "latest_version": "5.0.0"}])
        tool.invoke({
            "folder_name": "main",
            "ecosystem": "nodejs",
            "packages_json": packages,
        })

        assert backend.execute.call_count == 1
        cmd = backend.execute.call_args_list[0][0][0]
        assert "express@5.0.0" in cmd
