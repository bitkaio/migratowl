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

"""Point the sandbox's package tools at the configured registry mirrors."""

import posixpath
from collections.abc import Callable
from typing import Any

from langchain.tools import tool

from migratowl.agent.tools.update import q
from migratowl.registries import Registries


def create_configure_registries_tool(get_backend: Callable[[], Any], registries: Registries) -> Any:
    """Create a configure_registries tool that writes pip/npm/Go/Cargo/Maven config into the sandbox.

    Nothing is written when every registry is the public one. Credentials in the files are uploaded
    as content, so they appear in no command line, log or tool output.
    """

    @tool
    def configure_registries() -> str:
        """Configure the sandbox's package managers to use the configured registry mirrors."""
        files = registries.sandbox_files()
        if not files:
            return "Package registries: public defaults, nothing to configure."
        backend = get_backend()
        for directory in sorted({posixpath.dirname(path) for path in files}):
            backend.execute(f"mkdir -p {q(directory)}")
        responses = backend.upload_files([(path, content.encode()) for path, content in files.items()])
        failed = [f"{r.path}: {r.error}" for r in responses if r.error]
        if failed:
            return "Failed to configure package registries: " + "; ".join(failed)
        return "Package registries configured: " + ", ".join(sorted(files))

    return configure_registries
