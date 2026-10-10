# SPDX-License-Identifier: Apache-2.0

"""gather_evidence: run the ast-grep scanner inside the sandbox."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from migratowl.agent.tools.evidence import create_gather_evidence_tool
from migratowl.evidence import sandbox_scan
from tests.conftest import ExecResult

WS = "/home/user/workspace"


def _backend(output: str = '{"available": true, "packages": {}}', exit_code: int = 0, upload_error=None):
    backend = MagicMock()
    def execute(cmd: str) -> ExecResult:
        if cmd.startswith(("mkdir", "test -f")) or "mv -f" in cmd:
            return ExecResult(output="", exit_code=0)
        return ExecResult(output=output, exit_code=exit_code)

    backend.execute.side_effect = execute
    backend.upload_files.side_effect = lambda files: [SimpleNamespace(path=p, error=upload_error) for p, _ in files]
    return backend


def test_uploads_the_scanner_and_request_and_runs_it_on_source() -> None:
    backend = _backend()
    request = {"packages": [{"name": "express", "ecosystem": "nodejs"}]}

    out = create_gather_evidence_tool(lambda: backend, WS).invoke({"request_json": json.dumps(request)})

    uploaded = {p: c for call in backend.upload_files.call_args_list for p, c in call.args[0]}
    script_path = next(p for p in uploaded if p.endswith("sandbox_scan.py"))
    request_path = next(p for p in uploaded if p.endswith("request.json"))
    assert uploaded[script_path] == open(sandbox_scan.__file__, "rb").read()
    assert json.loads(uploaded[request_path]) == request
    run = backend.execute.call_args_list[-1].args[0]
    assert "python3" in run and script_path in run and request_path in run and f"{WS}/source" in run
    assert json.loads(out)["available"] is True


def test_a_failing_scanner_reports_unavailable() -> None:
    backend = _backend(output="Traceback: boom", exit_code=1)

    out = json.loads(create_gather_evidence_tool(lambda: backend, WS).invoke({"request_json": '{"packages": []}'}))

    assert out["available"] is False and "exit" in out["reason"]


def test_an_upload_failure_reports_unavailable() -> None:
    backend = _backend(upload_error="permission_denied")

    out = json.loads(create_gather_evidence_tool(lambda: backend, WS).invoke({"request_json": '{"packages": []}'}))

    assert out["available"] is False
    assert not any("python3" in c.args[0] for c in backend.execute.call_args_list)
