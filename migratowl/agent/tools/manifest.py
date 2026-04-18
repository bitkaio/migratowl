# SPDX-License-Identifier: Apache-2.0

"""Manifest read/patch tools — replacements for broken deepagents built-ins."""

from collections.abc import Callable
from typing import Any

from langchain.tools import tool

from migratowl.agent.tools.update import _manifest_patch_cmd


def create_read_manifest_tool(get_backend: Callable[[], Any], workspace_path: str) -> Any:
    """Create a read_manifest tool bound to a sandbox backend."""

    @tool
    def read_manifest(path: str) -> str:
        """Read a file from the sandbox by absolute path.

        Use this instead of the broken built-in read_file tool.
        """
        result = get_backend().execute(f"cat {path}")
        return result.output if result.exit_code == 0 else f"ERROR reading {path}: {result.output}"

    return read_manifest


def create_patch_manifest_tool(get_backend: Callable[[], Any]) -> Any:
    """Create a patch_manifest tool bound to a sandbox backend."""

    @tool
    def patch_manifest(path: str, old_string: str, new_string: str) -> str:
        """Edit a file in the sandbox via exact string replacement.

        Use this instead of the broken built-in edit_file tool.
        """
        cmd = _manifest_patch_cmd(path, old_string, new_string)
        result = get_backend().execute(cmd)
        return f"Patched {path}" if result.exit_code == 0 else f"ERROR patching {path}: {result.output}"

    return patch_manifest