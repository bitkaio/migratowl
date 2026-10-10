# SPDX-License-Identifier: Apache-2.0

"""upload_to_sandbox: files land at the path asked for, whichever runtime the sandbox uses."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from migratowl.agent.tools.files import upload_to_sandbox
from tests.conftest import ExecResult


def _backend(existing_after: set[str], upload_error: str | None = None) -> MagicMock:
    backend = MagicMock()
    backend.upload_files.side_effect = lambda files: [SimpleNamespace(path=p, error=upload_error) for p, _ in files]

    def execute(cmd: str) -> ExecResult:
        if cmd.startswith("test -f"):
            path = cmd.split("test -f ", 1)[1].strip("'")
            return ExecResult(output="", exit_code=0 if path in existing_after else 1)
        return ExecResult(output="", exit_code=0)

    backend.execute.side_effect = execute
    return backend


def test_files_confined_under_app_are_moved_to_their_path() -> None:
    # The agent-sandbox runtime writes uploads to /app/<file name> (observed live), older ones under /app/<path>.
    backend = _backend({"/home/user/.npmrc"})

    failed = upload_to_sandbox(backend, [("/home/user/.npmrc", b"registry=https://npm.corp/\n")])

    assert failed == []
    move = next(c.args[0] for c in backend.execute.call_args_list if "mv -f" in c.args[0])
    assert "/app/.npmrc" in move and "/app/home/user/.npmrc" in move
    assert "registry=" not in move  # content never travels in a command
    assert any(c.args[0].startswith("mkdir -p") for c in backend.execute.call_args_list)


def test_a_file_that_never_arrives_is_reported() -> None:
    backend = _backend(set())

    failed = upload_to_sandbox(backend, [("/home/user/a.txt", b"x")])

    assert failed == ["/home/user/a.txt: not found after upload"]


def test_upload_errors_are_reported() -> None:
    backend = _backend({"/home/user/a.txt"}, upload_error="permission_denied")

    assert upload_to_sandbox(backend, [("/home/user/a.txt", b"x")]) == ["/home/user/a.txt: permission_denied"]


def test_files_are_uploaded_one_at_a_time() -> None:
    # Two files with the same name in different directories must not overwrite each other in /app.
    backend = _backend({"/a/conf", "/b/conf"})

    assert upload_to_sandbox(backend, [("/a/conf", b"1"), ("/b/conf", b"2")]) == []
    assert [len(c.args[0]) for c in backend.upload_files.call_args_list] == [1, 1]
