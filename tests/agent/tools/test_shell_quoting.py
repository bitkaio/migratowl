# SPDX-License-Identifier: Apache-2.0

"""Commands sent to the sandbox must survive quoting and untrusted values.

The agent-sandbox runtime runs ``shlex.split(command)`` without a shell, and raw
mode runs ``/bin/sh -c command``; both tokenize the same way. Package names,
file paths and repo URLs come from untrusted manifests and webhook payloads, so
each must reach the sandbox as exactly one argument.
"""

import json
import shlex
from unittest.mock import MagicMock

from migratowl.agent.tools.clone import create_clone_repo_tool, create_copy_source_tool
from migratowl.agent.tools.execute import create_execute_project_tool
from migratowl.agent.tools.manifest import create_read_manifest_tool
from migratowl.agent.tools.scan import create_scan_dependencies_tool
from migratowl.agent.tools.update import create_update_dependencies_tool
from migratowl.agent.tools.validate import create_validate_project_tool
from tests.conftest import ExecResult

WS = "/home/user/workspace"
HOSTILE = "x; touch /tmp/pwned 'q' $(id)"


def _backend() -> MagicMock:
    backend = MagicMock()
    backend.execute.return_value = ExecResult(output="ok", exit_code=0)
    return backend


def _commands(backend: MagicMock) -> list[str]:
    return [call[0][0] for call in backend.execute.call_args_list]


def _inner(cmd: str) -> str:
    """The script a ``sh -c <script>`` command runs, split as the sandbox splits it."""
    argv = shlex.split(cmd)
    assert argv[:2] == ["sh", "-c"] and len(argv) == 3, f"not a single sh -c script: {argv}"
    return argv[2]


def _inner_tokens(cmd: str) -> list[str]:
    return shlex.split(_inner(cmd))


class TestShellWrappedCommands:
    def test_execute_project_keeps_quotes_in_agent_commands(self) -> None:
        backend = _backend()
        tool = create_execute_project_tool(lambda: backend, workspace_path=WS)
        tool.invoke({
            "folder_name": "main",
            "install_command": "pip install -e '.[tests]'",
            "test_command": "python3 -m pytest -k 'not slow'",
        })
        install_cmd, test_cmd = _commands(backend)
        assert _inner(install_cmd).endswith("pip install -e '.[tests]'")
        assert _inner(test_cmd).endswith("python3 -m pytest -k 'not slow'")

    def test_validate_python_keeps_extras_quoted(self) -> None:
        backend = _backend()
        create_validate_project_tool(lambda: backend, workspace_path=WS).invoke(
            {"folder_name": "main", "ecosystem": "python"}
        )
        assert "pip install -e '.[tests]'" in _inner(_commands(backend)[0])

    def test_validate_quotes_folder_path(self) -> None:
        backend = _backend()
        create_validate_project_tool(lambda: backend, workspace_path=WS).invoke(
            {"folder_name": "my pkg", "ecosystem": "go"}
        )
        tokens = _inner_tokens(_commands(backend)[0])
        assert tokens[tokens.index("cd") + 1] == f"{WS}/my pkg"

    def test_python_update_passes_hostile_name_as_one_argument(self) -> None:
        backend = _backend()
        create_update_dependencies_tool(lambda: backend, workspace_path=WS).invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": json.dumps([{"name": HOSTILE, "latest_version": "1.0"}]),
        })
        tokens = _inner_tokens(_commands(backend)[0])
        assert f"{HOSTILE}==1.0" in tokens  # pip argument and pin line
        assert tokens.count(f"{HOSTILE}==1.0") == 2

    def test_nodejs_update_passes_hostile_name_as_one_argument(self) -> None:
        backend = _backend()
        create_update_dependencies_tool(lambda: backend, workspace_path=WS).invoke({
            "folder_name": "main",
            "ecosystem": "nodejs",
            "packages_json": json.dumps([{"name": HOSTILE, "latest_version": "2.0.0"}]),
        })
        assert f"{HOSTILE}@2.0.0" in _inner_tokens(_commands(backend)[0])

    def test_unsupported_ecosystem_message_is_quoted(self) -> None:
        backend = _backend()
        create_update_dependencies_tool(lambda: backend, workspace_path=WS).invoke({
            "folder_name": "main",
            "ecosystem": HOSTILE,
            "packages_json": json.dumps([{"name": "a", "latest_version": "1"}]),
        })
        assert shlex.split(_commands(backend)[0]) == ["echo", f"Unsupported ecosystem: {HOSTILE}"]


class TestDirectCommands:
    def test_clone_passes_url_and_branch_as_single_arguments(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=2),  # ls source → missing
            ExecResult(output="", exit_code=0),  # git clone
            ExecResult(output="README.md", exit_code=0),  # verify
        ]
        create_clone_repo_tool(lambda: backend, workspace_path=WS).invoke(
            {"repo_url": f"https://example.com/{HOSTILE}", "branch": HOSTILE}
        )
        argv = shlex.split(_commands(backend)[1])
        assert argv[:2] == ["git", "clone"]
        assert argv[argv.index("--branch") + 1] == HOSTILE
        # `--` stops a URL like "--upload-pack=..." from being read as an option.
        assert argv[-3:] == ["--", f"https://example.com/{HOSTILE}", f"{WS}/source"]

    def test_copy_source_quotes_folder_name(self) -> None:
        backend = _backend()
        create_copy_source_tool(lambda: backend, workspace_path=WS).invoke({"folder_name": "my pkg"})
        argvs = [shlex.split(c) for c in _commands(backend)]
        assert ["mkdir", "-p", f"{WS}/my pkg"] in argvs
        assert ["cp", "-a", f"{WS}/source/.", f"{WS}/my pkg/"] in argvs

    def test_read_manifest_quotes_path(self) -> None:
        backend = _backend()
        create_read_manifest_tool(lambda: backend, workspace_path=WS).invoke({"path": f"{WS}/main/{HOSTILE}"})
        assert shlex.split(_commands(backend)[0]) == ["cat", f"{WS}/main/{HOSTILE}"]

    def test_scan_quotes_manifest_paths_found_in_repo(self) -> None:
        manifest = f"{WS}/source/odd dir/package.json"
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output=f"{manifest}\n", exit_code=0),  # find
            ExecResult(output='{"dependencies": {"a": "1.0.0"}}', exit_code=0),  # cat
        ]
        create_scan_dependencies_tool(lambda: backend, workspace_path=f"{WS}/source").invoke({})
        assert shlex.split(_commands(backend)[1]) == ["cat", manifest]
