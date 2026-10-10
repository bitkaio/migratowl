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

"""Put files into the sandbox at an exact path, in either sandbox mode."""

import posixpath
from typing import Any

from migratowl.agent.tools.update import _sh, q

# The agent-sandbox runtime server writes every upload to /app/<file name> (directories dropped; older
# runtimes keep the path under /app). Raw mode writes to the path as given.
_UPLOAD_ROOT = "/app"


def upload_to_sandbox(backend: Any, files: list[tuple[str, bytes]]) -> list[str]:
    """Upload ``(absolute path, content)`` pairs and make each file appear at its path.

    Files go one at a time so two with the same name cannot overwrite each other in /app. Content
    travels as an upload, never in a command line (it may hold credentials). Returns the failures as
    ``"path: reason"``; empty when every file is in place.
    """
    for directory in sorted({posixpath.dirname(path) for path, _ in files}):
        backend.execute(f"mkdir -p {q(directory)}")
    failed: list[str] = []
    for path, content in files:
        responses = backend.upload_files([(path, content)])
        errors = [f"{r.path}: {r.error}" for r in responses if r.error]
        if errors:
            failed += errors
            continue
        by_name = f"{_UPLOAD_ROOT}/{posixpath.basename(path)}"
        by_path = f"{_UPLOAD_ROOT}{path}"
        backend.execute(_sh(
            f"if [ -f {q(by_name)} ] && [ {q(by_name)} != {q(path)} ]; then mv -f {q(by_name)} {q(path)}; "
            f"elif [ -f {q(by_path)} ] && [ {q(by_path)} != {q(path)} ]; then mv -f {q(by_path)} {q(path)}; fi"
        ))
        if backend.execute(f"test -f {q(path)}").exit_code != 0:
            failed.append(f"{path}: not found after upload")
    return failed
