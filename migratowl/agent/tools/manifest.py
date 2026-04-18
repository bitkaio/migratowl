# Copyright bitkaio LLC
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