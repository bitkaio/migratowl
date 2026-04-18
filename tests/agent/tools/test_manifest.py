# SPDX-License-Identifier: Apache-2.0

"""Tests for manifest read/patch tools."""

from unittest.mock import MagicMock

from tests.conftest import ExecResult


class TestReadManifestTool:
    def _make_tool(self, backend: MagicMock):
        from migratowl.agent.tools.manifest import create_read_manifest_tool
        return create_read_manifest_tool(lambda: backend, workspace_path="/home/user/workspace")

    def test_reads_file_via_cat(self) -> None:
        backend = MagicMock()
        backend.execute.return_value = ExecResult(output="[dependencies]\nserde = '1.0'", exit_code=0)
        tool = self._make_tool(backend)

        result = tool.invoke({"path": "/home/user/workspace/main/Cargo.toml"})

        assert "serde" in result
        cmd = backend.execute.call_args[0][0]
        assert "cat" in cmd

    def test_returns_error_message_on_failure(self) -> None:
        backend = MagicMock()
        backend.execute.return_value = ExecResult(output="No such file", exit_code=1)
        tool = self._make_tool(backend)

        result = tool.invoke({"path": "/home/user/workspace/main/Cargo.toml"})

        assert result.startswith("ERROR")


class TestPatchManifestTool:
    def _make_tool(self, backend: MagicMock):
        from migratowl.agent.tools.manifest import create_patch_manifest_tool
        return create_patch_manifest_tool(lambda: backend)

    def test_patches_file_via_python3_oneliner(self) -> None:
        backend = MagicMock()
        backend.execute.return_value = ExecResult(output="", exit_code=0)
        tool = self._make_tool(backend)

        tool.invoke({
            "path": "/home/user/workspace/main/Cargo.toml",
            "old_string": "2.33.0",
            "new_string": "4.6.0",
        })

        cmd = backend.execute.call_args[0][0]
        assert "python3 -c" in cmd
        assert "replace" in cmd

    def test_command_invokes_python3_directly(self) -> None:
        """patch_manifest must call python3 directly, not via sh -c.

        sh -c '... python3 -c 'script'' causes nested single-quote
        breakage when parsed by shlex.split() in the sandbox backend.
        """
        backend = MagicMock()
        backend.execute.return_value = ExecResult(output="", exit_code=0)
        tool = self._make_tool(backend)

        tool.invoke({
            "path": "/home/user/workspace/main/Cargo.toml",
            "old_string": "2.33.0",
            "new_string": "4.6.0",
        })

        cmd = backend.execute.call_args[0][0]
        assert cmd.startswith("python3 -c ")

    def test_uses_shlex_quoting(self) -> None:
        backend = MagicMock()
        backend.execute.return_value = ExecResult(output="", exit_code=0)
        tool = self._make_tool(backend)

        # path with spaces and old_string with single quotes
        tool.invoke({
            "path": "/home/user/my workspace/Cargo.toml",
            "old_string": "version = '2.0'",
            "new_string": "version = '4.0'",
        })

        cmd = backend.execute.call_args[0][0]
        # shlex.quote wraps in single quotes or escapes — just verify cmd is a str
        # and the values are present somewhere (safely escaped)
        assert isinstance(cmd, str)
        assert "my workspace" in cmd or "my\\ workspace" in cmd

    def test_returns_error_on_failure(self) -> None:
        backend = MagicMock()
        backend.execute.return_value = ExecResult(output="permission denied", exit_code=1)
        tool = self._make_tool(backend)

        result = tool.invoke({
            "path": "/home/user/workspace/main/Cargo.toml",
            "old_string": "2.33.0",
            "new_string": "4.6.0",
        })

        assert result.startswith("ERROR")