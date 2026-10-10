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

"""Run the ast-grep evidence scanner inside the sandbox, on the cloned source."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langchain.tools import tool

from migratowl.agent.tools.files import upload_to_sandbox
from migratowl.agent.tools.update import _sh, q
from migratowl.evidence import sandbox_scan

_SCRIPT = Path(sandbox_scan.__file__).read_bytes()


def _unavailable(reason: str) -> str:
    return json.dumps({"available": False, "reason": reason, "packages": {}})


def create_gather_evidence_tool(get_backend: Callable[[], Any], workspace_path: str) -> Any:
    """Create the gather_evidence tool: upload the scanner and a request, run it on ``source/``."""
    work_dir = f"{workspace_path}/.migratowl-evidence"
    script, request_file = f"{work_dir}/sandbox_scan.py", f"{work_dir}/request.json"

    @tool
    def gather_evidence(request_json: str) -> str:
        """Parse source/ with ast-grep: who imports each package, whether tests reach it, rule hits (JSON)."""
        backend = get_backend()
        failed = upload_to_sandbox(backend, [(script, _SCRIPT), (request_file, request_json.encode())])
        if failed:
            return _unavailable("upload failed: " + "; ".join(failed))
        result = backend.execute(_sh(f"python3 {q(script)} {q(request_file)} {q(workspace_path + '/source')}"))
        if result.exit_code != 0:
            return _unavailable(f"scanner exited {result.exit_code}: {result.output.strip()[-300:]}")
        return result.output.strip()

    return gather_evidence
