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

"""Execute project tool for the Migratowl agent."""

import json
from collections.abc import Callable
from typing import Any

from langchain.tools import tool


def create_execute_project_tool(
    get_backend: Callable[[], Any],
    workspace_path: str,
    max_output_chars: int = 50_000,
) -> Any:
    """Create an execute_project tool bound to a sandbox backend.

    Args:
        get_backend: Callable that returns a sandbox backend.
        workspace_path: Root workspace path inside the sandbox.
        max_output_chars: Maximum characters to keep from stdout/stderr.
    """

    @tool
    def execute_project(
        folder_name: str,
        install_command: str,
        test_command: str,
    ) -> str:
        """Run install and test commands in a working folder and return results as JSON.

        Args:
            folder_name: Target folder name (e.g. "main", "requests").
            install_command: Command to install dependencies (e.g. "pip install -e .").
            test_command: Command to run tests (e.g. "pytest -x --tb=short").
        """
        backend = get_backend()
        folder_path = f"{workspace_path}/{folder_name}"

        install_result = backend.execute(
            f"sh -c 'export PIP_BREAK_SYSTEM_PACKAGES=1 && cd {folder_path} && {install_command}'"
        )
        test_result = backend.execute(
            f"sh -c 'export PIP_BREAK_SYSTEM_PACKAGES=1 && cd {folder_path} && {test_command}'"
        )

        return json.dumps({
            "install": _format_result(install_result, install_command, max_output_chars),
            "test": _format_result(test_result, test_command, max_output_chars),
        })

    return execute_project


def _format_result(result: Any, command: str, max_chars: int) -> dict[str, Any]:
    """Format an execution result, truncating output if needed."""
    stdout = result.output
    truncated = len(stdout) > max_chars
    if truncated:
        stdout = stdout[:max_chars]

    return {
        "command_run": command,
        "exit_code": result.exit_code,
        "stdout": stdout,
        "truncated": truncated,
    }