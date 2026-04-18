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

"""Tests for execute_project tool."""

import json
from unittest.mock import MagicMock

from migratowl.agent.tools.execute import create_execute_project_tool
from tests.conftest import ExecResult

DEFAULT_WORKSPACE = "/home/user/workspace"


class TestExecuteProjectTool:
    def test_successful_run(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="Successfully installed\n", exit_code=0),  # install
            ExecResult(output="3 passed\n", exit_code=0),  # test
        ]
        tool = create_execute_project_tool(
            lambda: backend, workspace_path=DEFAULT_WORKSPACE, max_output_chars=50_000,
        )

        result = tool.invoke({
            "folder_name": "main",
            "install_command": "pip install -e .",
            "test_command": "pytest -x --tb=short",
        })

        parsed = json.loads(result)
        assert parsed["install"]["exit_code"] == 0
        assert parsed["test"]["exit_code"] == 0
        assert "Successfully installed" in parsed["install"]["stdout"]
        assert "3 passed" in parsed["test"]["stdout"]

    def test_failed_tests(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="Successfully installed\n", exit_code=0),
            ExecResult(output="FAILED test_foo.py::test_bar\n", exit_code=1),
        ]
        tool = create_execute_project_tool(
            lambda: backend, workspace_path=DEFAULT_WORKSPACE, max_output_chars=50_000,
        )

        result = tool.invoke({
            "folder_name": "main",
            "install_command": "pip install -e .",
            "test_command": "pytest -x --tb=short",
        })

        parsed = json.loads(result)
        assert parsed["install"]["exit_code"] == 0
        assert parsed["test"]["exit_code"] == 1
        assert "FAILED" in parsed["test"]["stdout"]

    def test_install_failure(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="ERROR: Could not install\n", exit_code=1),
            ExecResult(output="", exit_code=1),  # test also fails
        ]
        tool = create_execute_project_tool(
            lambda: backend, workspace_path=DEFAULT_WORKSPACE, max_output_chars=50_000,
        )

        result = tool.invoke({
            "folder_name": "main",
            "install_command": "pip install -e .",
            "test_command": "pytest -x --tb=short",
        })

        parsed = json.loads(result)
        assert parsed["install"]["exit_code"] == 1

    def test_output_truncation(self) -> None:
        long_output = "x" * 200
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output=long_output, exit_code=0),
            ExecResult(output="ok\n", exit_code=0),
        ]
        tool = create_execute_project_tool(
            lambda: backend, workspace_path=DEFAULT_WORKSPACE, max_output_chars=100,
        )

        result = tool.invoke({
            "folder_name": "main",
            "install_command": "pip install -e .",
            "test_command": "pytest",
        })

        parsed = json.loads(result)
        assert parsed["install"]["truncated"] is True
        assert len(parsed["install"]["stdout"]) == 100
        assert parsed["test"]["truncated"] is False

    def test_commands_wrapped_in_shell(self) -> None:
        """Commands must be wrapped in sh -c since sandbox execute doesn't use a shell."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=0),
        ]
        tool = create_execute_project_tool(
            lambda: backend, workspace_path=DEFAULT_WORKSPACE, max_output_chars=50_000,
        )

        tool.invoke({
            "folder_name": "main",
            "install_command": "pip install -e .",
            "test_command": "pytest -x",
        })

        for call in backend.execute.call_args_list:
            cmd = call[0][0]
            assert cmd.startswith("sh -c "), f"Command not shell-wrapped: {cmd}"

    def test_sets_break_system_packages(self) -> None:
        """Commands must set PIP_BREAK_SYSTEM_PACKAGES=1 for PEP 668 containers."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=0),
        ]
        tool = create_execute_project_tool(
            lambda: backend, workspace_path=DEFAULT_WORKSPACE, max_output_chars=50_000,
        )

        tool.invoke({
            "folder_name": "main",
            "install_command": "pip install -e .",
            "test_command": "pytest -x",
        })

        for call in backend.execute.call_args_list:
            cmd = call[0][0]
            assert "PIP_BREAK_SYSTEM_PACKAGES=1" in cmd, f"Missing PIP env var: {cmd}"

    def test_commands_run_in_correct_folder(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=0),
        ]
        tool = create_execute_project_tool(
            lambda: backend, workspace_path=DEFAULT_WORKSPACE, max_output_chars=50_000,
        )

        tool.invoke({
            "folder_name": "requests",
            "install_command": "pip install -e .",
            "test_command": "pytest -x",
        })

        install_cmd = backend.execute.call_args_list[0][0][0]
        test_cmd = backend.execute.call_args_list[1][0][0]
        assert f"{DEFAULT_WORKSPACE}/requests" in install_cmd
        assert f"{DEFAULT_WORKSPACE}/requests" in test_cmd
