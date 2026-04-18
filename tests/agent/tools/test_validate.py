# SPDX-License-Identifier: Apache-2.0

"""Tests for validate_project tool."""

import json
from unittest.mock import MagicMock

from migratowl.agent.tools.validate import create_validate_project_tool
from tests.conftest import ExecResult

DEFAULT_WORKSPACE = "/home/user/workspace"


def _make_tool(backend: MagicMock):
    return create_validate_project_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)


class TestValidateGo:
    def test_build_is_first_step(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # go build
            ExecResult(output="", exit_code=0),  # find *_test.go → empty
        ]
        _make_tool(backend).invoke({"folder_name": "main", "ecosystem": "go"})

        first_cmd = backend.execute.call_args_list[0][0][0]
        assert "go build" in first_cmd

    def test_stops_after_build_failure(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="compile error\n", exit_code=1),
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "go"}))

        assert backend.execute.call_count == 1
        assert result["passed"] is False

    def test_runs_tests_when_test_files_found(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output=f"{DEFAULT_WORKSPACE}/main/foo_test.go\n", exit_code=0),
            ExecResult(output="ok github.com/foo 0.5s\n", exit_code=0),
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "go"}))

        assert backend.execute.call_count == 3
        test_cmd = backend.execute.call_args_list[2][0][0]
        assert "go test" in test_cmd
        assert result["passed"] is True

    def test_skips_test_when_no_test_files(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # go build
            ExecResult(output="", exit_code=0),  # find → empty output
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "go"}))

        assert backend.execute.call_count == 2
        assert result["passed"] is True
        test_step = next(s for s in result["steps"] if s["name"] == "test")
        assert test_step.get("skipped") is True

    def test_failed_tests_mark_passed_false(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output=f"{DEFAULT_WORKSPACE}/main/foo_test.go\n", exit_code=0),
            ExecResult(output="FAIL github.com/foo\n", exit_code=1),
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "go"}))

        assert result["passed"] is False


class TestValidateRust:
    def test_build_is_first_step(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # cargo build
            ExecResult(output="", exit_code=0),  # detect → empty
        ]
        _make_tool(backend).invoke({"folder_name": "main", "ecosystem": "rust"})

        first_cmd = backend.execute.call_args_list[0][0][0]
        assert "cargo build" in first_cmd

    def test_stops_after_build_failure(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="error[E0412]: cannot find type\n", exit_code=1),
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "rust"}))

        assert backend.execute.call_count == 1
        assert result["passed"] is False

    def test_runs_tests_when_detected(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output=f"{DEFAULT_WORKSPACE}/main/src/lib.rs\n", exit_code=0),
            ExecResult(output="running 3 tests\ntest result: ok.\n", exit_code=0),
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "rust"}))

        assert backend.execute.call_count == 3
        test_cmd = backend.execute.call_args_list[2][0][0]
        assert "cargo test" in test_cmd
        assert result["passed"] is True

    def test_skips_test_when_no_test_fns(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # cargo build
            ExecResult(output="", exit_code=0),  # detect → empty
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "rust"}))

        assert backend.execute.call_count == 2
        assert result["passed"] is True


class TestValidatePython:
    def test_installs_then_runs_pytest(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=0),              # detect → found (exit 0)
            ExecResult(output="5 passed in 0.3s\n", exit_code=0),
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "python"}))

        install_cmd = backend.execute.call_args_list[0][0][0]
        assert "pip install" in install_cmd
        test_cmd = backend.execute.call_args_list[2][0][0]
        assert "pytest" in test_cmd
        assert result["passed"] is True

    def test_stops_after_install_failure(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="ERROR: no file found\n", exit_code=1),
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "python"}))

        assert backend.execute.call_count == 1
        assert result["passed"] is False

    def test_skips_pytest_when_not_detected(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # install
            ExecResult(output="", exit_code=1),  # detect → not found (exit 1)
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "python"}))

        assert backend.execute.call_count == 2
        assert result["passed"] is True
        test_step = next(s for s in result["steps"] if s["name"] == "test")
        assert test_step.get("skipped") is True

    def test_uses_python_module_to_run_pytest(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),   # install
            ExecResult(output="", exit_code=0),   # detect → found
            ExecResult(output="5 passed\n", exit_code=0),
        ]
        _make_tool(backend).invoke({"folder_name": "main", "ecosystem": "python"})

        test_cmd = backend.execute.call_args_list[2][0][0]
        assert "python3 -m pytest" in test_cmd  # must use python3, not python

    def test_install_tries_test_extras_first(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),   # install
            ExecResult(output="", exit_code=1),   # detect → not found (skip test)
        ]
        _make_tool(backend).invoke({"folder_name": "main", "ecosystem": "python"})

        install_cmd = backend.execute.call_args_list[0][0][0]
        assert ".[tests]" in install_cmd  # RED: currently bare "pip install -e ."


class TestValidateNodeJS:
    def test_runs_npm_install(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # npm install
            ExecResult(output="", exit_code=1),  # tsconfig → not found
            ExecResult(output="", exit_code=1),  # test script → not found
        ]
        _make_tool(backend).invoke({"folder_name": "main", "ecosystem": "nodejs"})

        install_cmd = backend.execute.call_args_list[0][0][0]
        assert "npm install" in install_cmd

    def test_runs_tsc_when_tsconfig_present(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # npm install
            ExecResult(output="", exit_code=0),  # tsconfig → found
            ExecResult(output="", exit_code=0),  # tsc --noEmit
            ExecResult(output="", exit_code=1),  # test script → not found
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "nodejs"}))

        tsc_cmd = backend.execute.call_args_list[2][0][0]
        assert "tsc" in tsc_cmd
        assert "--noEmit" in tsc_cmd
        assert result["passed"] is True

    def test_stops_after_tsc_failure(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=0),              # tsconfig → found
            ExecResult(output="TS2339: Property not found\n", exit_code=1),  # tsc fails
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "nodejs"}))

        assert backend.execute.call_count == 3
        assert result["passed"] is False

    def test_runs_npm_test_when_test_script_present(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # npm install
            ExecResult(output="", exit_code=1),  # tsconfig → not found
            ExecResult(output="", exit_code=0),  # test script → found
            ExecResult(output="All tests passed\n", exit_code=0),
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "nodejs"}))

        test_cmd = backend.execute.call_args_list[3][0][0]
        assert "npm test" in test_cmd
        assert result["passed"] is True

    def test_skips_npm_test_when_no_test_script(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # npm install
            ExecResult(output="", exit_code=1),  # tsconfig → not found
            ExecResult(output="", exit_code=1),  # test script → not found
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "nodejs"}))

        assert backend.execute.call_count == 3
        assert result["passed"] is True
        test_step = next(s for s in result["steps"] if s["name"] == "test")
        assert test_step.get("skipped") is True


class TestValidateJava:
    def test_maven_build_is_first_step(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="yes", exit_code=0),   # test -f pom.xml
            ExecResult(output="", exit_code=0),       # mvn compile
            ExecResult(output="", exit_code=0),       # find src/test → empty
        ]
        _make_tool(backend).invoke({"folder_name": "main", "ecosystem": "java"})

        build_cmd = backend.execute.call_args_list[1][0][0]
        assert "mvn compile" in build_cmd

    def test_maven_stops_after_build_failure(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="yes", exit_code=0),    # test -f pom.xml
            ExecResult(output="BUILD FAILURE\n", exit_code=1),  # mvn compile
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "java"}))

        assert backend.execute.call_count == 2
        assert result["passed"] is False

    def test_maven_runs_tests_when_src_test_found(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="yes", exit_code=0),   # test -f pom.xml
            ExecResult(output="", exit_code=0),       # mvn compile
            ExecResult(output=f"{DEFAULT_WORKSPACE}/main/src/test/java/FooTest.java\n", exit_code=0),
            ExecResult(output="Tests run: 3\n", exit_code=0),  # mvn test
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "java"}))

        assert backend.execute.call_count == 4
        test_cmd = backend.execute.call_args_list[3][0][0]
        assert "mvn test" in test_cmd
        assert result["passed"] is True

    def test_maven_skips_test_when_no_src_test(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="yes", exit_code=0),   # test -f pom.xml
            ExecResult(output="", exit_code=0),       # mvn compile
            ExecResult(output="", exit_code=0),       # find src/test → empty
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "java"}))

        assert result["passed"] is True
        test_step = next(s for s in result["steps"] if s["name"] == "test")
        assert test_step.get("skipped") is True

    def test_gradle_build_used_when_no_pom(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=1),   # test -f pom.xml → not found
            ExecResult(output="", exit_code=0),   # gradle compileJava
            ExecResult(output="", exit_code=0),   # find src/test → empty
        ]
        _make_tool(backend).invoke({"folder_name": "main", "ecosystem": "java"})

        build_cmd = backend.execute.call_args_list[1][0][0]
        assert "gradle compileJava" in build_cmd

    def test_failed_tests_mark_passed_false(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="yes", exit_code=0),
            ExecResult(output="", exit_code=0),
            ExecResult(output=f"{DEFAULT_WORKSPACE}/main/src/test/java/FooTest.java\n", exit_code=0),
            ExecResult(output="BUILD FAILURE\n", exit_code=1),
        ]
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "java"}))

        assert result["passed"] is False


class TestValidateProjectOutput:
    def test_returns_valid_json_with_steps_and_passed(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=0),
        ]
        result = _make_tool(backend).invoke({"folder_name": "main", "ecosystem": "go"})
        parsed = json.loads(result)
        assert "steps" in parsed
        assert "passed" in parsed
        assert isinstance(parsed["steps"], list)

    def test_unsupported_ecosystem_returns_error(self) -> None:
        backend = MagicMock()
        result = json.loads(_make_tool(backend).invoke({"folder_name": "main", "ecosystem": "ruby"}))
        assert "error" in result