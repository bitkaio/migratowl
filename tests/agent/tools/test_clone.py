# SPDX-License-Identifier: Apache-2.0

"""Tests for clone_repo and copy_source tools."""

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

from migratowl.agent.tools.clone import create_clone_repo_tool, create_copy_source_tool
from tests.conftest import ExecResult

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
            ),  # clone --branch main FAILS
            ExecResult(output="", exit_code=0),  # rm -rf cleanup
            ExecResult(
                output="fatal: repository 'https://github.com/bad/repo' not found",
                exit_code=128,
            ),  # clone without --branch ALSO FAILS
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
            "git clone --branch develop --depth 1 -- "
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

    def test_main_branch_fails_fallback_succeeds(self) -> None:
        """branch='main' (default) fails, retry without --branch succeeds."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),          # (1) ls source/ — empty
            ExecResult(output="error", exit_code=128),   # (2) git clone --branch main FAILS
            ExecResult(output="", exit_code=0),          # (3) rm -rf cleanup
            ExecResult(output="Cloning...", exit_code=0),  # (4) git clone without --branch SUCCEEDS
            ExecResult(output="README.md\n", exit_code=0),  # (5) ls verify
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        assert "success" in result.lower()
        # Call 3 (index 2) must be rm -rf
        rm_cmd = backend.execute.call_args_list[2][0][0]
        assert "rm -rf" in rm_cmd
        # Call 4 (index 3) must NOT contain --branch
        retry_cmd = backend.execute.call_args_list[3][0][0]
        assert "--branch" not in retry_cmd

    def test_main_branch_fails_fallback_also_fails(self) -> None:
        """branch='main' fails and the fallback default-branch clone also fails."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),         # (1) ls source/ — empty
            ExecResult(output="error1", exit_code=128), # (2) git clone --branch main FAILS
            ExecResult(output="", exit_code=0),         # (3) rm -rf cleanup
            ExecResult(output="error2", exit_code=1),   # (4) git clone without --branch ALSO FAILS
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests"})

        assert "failed" in result.lower()

    def test_explicit_non_main_branch_fails_no_fallback(self) -> None:
        """branch='develop' fails — no fallback attempted, only 2 execute calls."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),        # (1) ls source/ — empty
            ExecResult(output="error", exit_code=128), # (2) git clone --branch develop FAILS
        ]
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"repo_url": "https://github.com/psf/requests", "branch": "develop"})

        assert backend.execute.call_count == 2
        assert "failed" in result.lower()


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

class TestPrivateRepoClone:
    """Credentials reach git as a per-host header, never in the URL, .git/config, or messages."""

    SECRET = "ghp_" + "a" * 36

    @staticmethod
    def _backend(clone_exit: int = 0, clone_output: str = "") -> MagicMock:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # ls source/ — empty
            ExecResult(output=clone_output, exit_code=clone_exit),  # git clone
            ExecResult(output="README.md\n", exit_code=0),  # ls verify
        ]
        return backend

    @staticmethod
    def _header_value(cmd: str, host: str) -> str:
        import base64
        import shlex

        key = f"http.https://{host}/.extraHeader=Authorization: Basic "
        arg = next(a for a in shlex.split(cmd) if a.startswith(key))
        return base64.b64decode(arg.removeprefix(key)).decode()

    def test_configured_token_is_sent_as_scoped_header(self) -> None:
        backend = self._backend()
        tool = create_clone_repo_tool(
            lambda: backend, workspace_path=DEFAULT_WORKSPACE,
            tokens={"github.com": ("x-access-token", self.SECRET)},
        )

        tool.invoke({"repo_url": "https://github.com/o/private"})

        cmd = backend.execute.call_args_list[1][0][0]
        assert self._header_value(cmd, "github.com") == f"x-access-token:{self.SECRET}"
        assert "-- https://github.com/o/private " in cmd  # URL stays clean

    def test_token_is_not_sent_to_other_hosts(self) -> None:
        backend = self._backend()
        tool = create_clone_repo_tool(
            lambda: backend, workspace_path=DEFAULT_WORKSPACE,
            tokens={"github.com": ("x-access-token", self.SECRET)},
        )

        tool.invoke({"repo_url": "https://example.org/o/repo"})

        assert self.SECRET not in backend.execute.call_args_list[1][0][0]

    def test_credentials_in_url_move_into_the_header(self) -> None:
        backend = self._backend()
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        tool.invoke({"repo_url": f"https://alice:{self.SECRET}@gitlab.com/g/p.git"})

        cmd = backend.execute.call_args_list[1][0][0]
        assert self._header_value(cmd, "gitlab.com") == f"alice:{self.SECRET}"
        assert "-- https://gitlab.com/g/p.git " in cmd

    def test_failure_message_hides_the_credentials(self) -> None:
        backend = _fake_git(lambda cmd: ExecResult(output=f"fatal: could not read {self.SECRET}", exit_code=128))
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({"repo_url": f"https://bob:{self.SECRET}@github.com/o/r", "branch": "dev"})

        assert result.startswith("Failed")
        assert self.SECRET not in result
        assert "bob" not in result

    def test_default_branch_retry_keeps_the_header(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=128),  # --branch main fails
            ExecResult(output="", exit_code=0),  # rm -rf
            ExecResult(output="", exit_code=0),  # retry without --branch
            ExecResult(output="README.md\n", exit_code=0),
        ]
        tool = create_clone_repo_tool(
            lambda: backend, workspace_path=DEFAULT_WORKSPACE,
            tokens={"github.com": ("x-access-token", self.SECRET)},
        )

        tool.invoke({"repo_url": "https://github.com/o/private"})

        retry = backend.execute.call_args_list[3][0][0]
        assert self._header_value(retry, "github.com") == f"x-access-token:{self.SECRET}"

    def test_public_clone_command_is_unchanged(self) -> None:
        backend = self._backend()
        tool = create_clone_repo_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        tool.invoke({"repo_url": "https://github.com/psf/requests"})

        assert backend.execute.call_args_list[1][0][0] == (
            "git clone --branch main --depth 1 -- https://github.com/psf/requests /home/user/workspace/source"
        )


class TestCloneTokensFromSettings:
    def test_github_and_gitlab_hosts(self) -> None:
        from migratowl.agent.tools.clone import clone_tokens
        from migratowl.config import Settings

        settings = Settings(github_token="ghp_x", gitlab_token="glpat-y",
                            github_api_url="https://api.github.com", gitlab_api_url="https://gitlab.com/api/v4")
        assert clone_tokens(settings) == {
            "github.com": ("x-access-token", "ghp_x"),
            "gitlab.com": ("oauth2", "glpat-y"),
        }

    def test_enterprise_hosts_and_missing_tokens(self) -> None:
        from migratowl.agent.tools.clone import clone_tokens
        from migratowl.config import Settings

        settings = Settings(github_token="ghp_x", gitlab_token="",
                            github_api_url="https://github.corp.com/api/v3")
        assert clone_tokens(settings) == {"github.corp.com": ("x-access-token", "ghp_x")}


def _fake_git(clone: Callable[[str], ExecResult]) -> MagicMock:
    """Backend answering by command: empty ``ls`` before a clone, ``clone`` for git, success otherwise."""
    state = {"cloned": False}

    def execute(cmd: str) -> ExecResult:
        if "git" in cmd and "clone" in cmd:
            result = clone(cmd)
            state["cloned"] = result.exit_code == 0
            return result
        if cmd.startswith("ls"):
            return ExecResult(output="README.md\n" if state["cloned"] else "", exit_code=0)
        return ExecResult(output="", exit_code=0)

    backend = MagicMock()
    backend.execute.side_effect = execute
    return backend


class TestRejectedCredentials:
    """A token the host rejects (e.g. a CI job token) must not break cloning a public repo."""

    SECRET = "glcbt-" + "c" * 30

    def _tool(self, backend: MagicMock) -> Any:
        return create_clone_repo_tool(
            lambda: backend, workspace_path=DEFAULT_WORKSPACE, tokens={"gitlab.com": ("oauth2", self.SECRET)}
        )

    def test_public_repo_is_cloned_anonymously_after_auth_failure(self) -> None:
        backend = _fake_git(lambda cmd: (
            ExecResult(output="fatal: Authentication failed", exit_code=128)
            if "extraHeader" in cmd else ExecResult(output="", exit_code=0)
        ))

        result = self._tool(backend).invoke({"repo_url": "https://gitlab.com/g/public"})

        assert result.startswith("Successfully cloned")
        clones = [c[0][0] for c in backend.execute.call_args_list if "clone" in c[0][0]]
        assert "extraHeader" not in clones[-1] and self.SECRET not in clones[-1]

    def test_private_repo_with_bad_token_reports_the_first_failure(self) -> None:
        backend = _fake_git(lambda cmd: ExecResult(
            output="fatal: Authentication failed" if "extraHeader" in cmd else "fatal: could not read Username",
            exit_code=128,
        ))

        result = self._tool(backend).invoke({"repo_url": "https://gitlab.com/g/private", "branch": "dev"})

        assert result.startswith("Failed")
        assert "Authentication failed" in result
        assert self.SECRET not in result
