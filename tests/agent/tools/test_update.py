"""Tests for update_dependencies tool."""

import json
from unittest.mock import MagicMock

from migratowl.agent.tools.update import (
    _is_major_bump,
    create_update_dependencies_tool,
)
from tests.conftest import ExecResult

DEFAULT_WORKSPACE = "/home/user/workspace"


class TestUpdateDependenciesTool:
    def test_python_single_package(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # pip install requests==2.31.0
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "requests", "latest_version": "2.31.0"}])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        pip_cmd = backend.execute.call_args_list[0][0][0]
        assert "pip install requests==2.31.0" in pip_cmd
        assert f"{DEFAULT_WORKSPACE}/main" in pip_cmd
        assert "success" in result.lower() or "updated" in result.lower()

    def test_python_multiple_packages(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # pip install requests==2.31.0
            ExecResult(output="", exit_code=0),  # pip install flask==3.0.0
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([
            {"name": "requests", "latest_version": "2.31.0"},
            {"name": "flask", "latest_version": "3.0.0"},
        ])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        assert backend.execute.call_count == 2
        assert "requests" in result and "flask" in result

    def test_nodejs_packages(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="added 1 package\n", exit_code=0),  # npm install express@5.0.0
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "express", "latest_version": "5.0.0"}])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "nodejs",
            "packages_json": packages,
        })

        npm_cmd = backend.execute.call_args_list[0][0][0]
        assert "npm install express@5.0.0" in npm_cmd
        assert f"{DEFAULT_WORKSPACE}/main" in npm_cmd

    def test_go_packages(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # go get
            ExecResult(output="", exit_code=0),  # go mod tidy
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "github.com/gin-gonic/gin", "latest_version": "1.9.1"}])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "go",
            "packages_json": packages,
        })

        go_cmd = backend.execute.call_args_list[0][0][0]
        assert "go get github.com/gin-gonic/gin@v1.9.1" in go_cmd
        tidy_cmd = backend.execute.call_args_list[1][0][0]
        assert "go mod tidy" in tidy_cmd

    def test_rust_packages(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # cargo update
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "serde", "latest_version": "1.0.200"}])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "rust",
            "packages_json": packages,
        })

        cargo_cmd = backend.execute.call_args_list[0][0][0]
        assert "cargo update -p serde --precise 1.0.200" in cargo_cmd

    def test_failed_update(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="ERROR: No matching distribution found\n", exit_code=1),
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "nonexistent", "latest_version": "1.0.0"}])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        assert "fail" in result.lower() or "error" in result.lower()

    def test_commands_wrapped_in_shell(self) -> None:
        """Commands must be wrapped in sh -c since sandbox execute doesn't use a shell."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # pip install
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "requests", "latest_version": "2.31.0"}])
        tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        for call in backend.execute.call_args_list:
            cmd = call[0][0]
            assert cmd.startswith("sh -c "), f"Command not shell-wrapped: {cmd}"

    def test_python_sets_break_system_packages(self) -> None:
        """Python pip commands must set PIP_BREAK_SYSTEM_PACKAGES=1 for PEP 668 containers."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "requests", "latest_version": "2.31.0"}])
        tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        for call in backend.execute.call_args_list:
            cmd = call[0][0]
            assert "PIP_BREAK_SYSTEM_PACKAGES=1" in cmd, f"Missing PIP env var: {cmd}"

    def test_empty_package_list(self) -> None:
        backend = MagicMock()
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([])
        result = tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        backend.execute.assert_not_called()
        assert "0" in result or "no packages" in result.lower() or "success" in result.lower()

    def test_does_not_run_install_command_after_python_update(self) -> None:
        """update_dependencies must NOT run install_command — that belongs to execute_project."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # pip install pkg==ver only
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "requests", "latest_version": "2.31.0"}])
        tool.invoke({
            "folder_name": "main",
            "ecosystem": "python",
            "packages_json": packages,
        })

        assert backend.execute.call_count == 1
        cmd = backend.execute.call_args_list[0][0][0]
        assert "pip install -e" not in cmd  # no install_command re-run
        assert "requests==2.31.0" in cmd

    def test_does_not_run_install_command_after_nodejs_update(self) -> None:
        """update_dependencies must NOT run install_command — that belongs to execute_project."""
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # npm install pkg@ver only
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "express", "latest_version": "5.0.0"}])
        tool.invoke({
            "folder_name": "main",
            "ecosystem": "nodejs",
            "packages_json": packages,
        })

        assert backend.execute.call_count == 1
        cmd = backend.execute.call_args_list[0][0][0]
        assert "express@5.0.0" in cmd


class TestIsMajorBump:
    def test_same_major_is_not_bump(self) -> None:
        assert _is_major_bump("1.0.0", "1.9.0") is False

    def test_different_major_is_bump(self) -> None:
        assert _is_major_bump("2.33.0", "4.6.0") is True

    def test_caret_prefix_stripped(self) -> None:
        assert _is_major_bump("^2", "4.0.0") is True

    def test_missing_current_returns_false(self) -> None:
        assert _is_major_bump("", "4.0.0") is False


class TestRustUpdateCommand:
    def test_rust_uses_versioned_specifier_when_current_given(self) -> None:
        backend = MagicMock()
        backend.execute.return_value = ExecResult(output="", exit_code=0)
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{
            "name": "serde",
            "current_version": "1.0.109",
            "latest_version": "1.0.200",
        }])
        tool.invoke({"folder_name": "main", "ecosystem": "rust", "packages_json": packages})

        cmd = backend.execute.call_args_list[0][0][0]
        # Uses major version only (@1) to match any 1.x.y in the lockfile
        assert "cargo update -p serde@1 --precise 1.0.200" in cmd

    def test_rust_no_versioned_specifier_without_current(self) -> None:
        backend = MagicMock()
        backend.execute.return_value = ExecResult(output="", exit_code=0)
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "serde", "latest_version": "1.0.200"}])
        tool.invoke({"folder_name": "main", "ecosystem": "rust", "packages_json": packages})

        cmd = backend.execute.call_args_list[0][0][0]
        assert "cargo update -p serde --precise 1.0.200" in cmd

    def test_rust_major_bump_produces_two_commands(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # manifest patch
            ExecResult(output="", exit_code=0),  # cargo check
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{
            "name": "clap",
            "current_version": "2.33.0",
            "latest_version": "4.6.0",
            "manifest_path": "dotenv/Cargo.toml",
        }])
        tool.invoke({"folder_name": "main", "ecosystem": "rust", "packages_json": packages})

        assert backend.execute.call_count == 2
        first_cmd = backend.execute.call_args_list[0][0][0]
        assert "python3 -c" in first_cmd
        assert "replace" in first_cmd
        second_cmd = backend.execute.call_args_list[1][0][0]
        assert "cargo check" in second_cmd

    def test_rust_major_bump_patch_targets_dependency_line(self) -> None:
        """old_string must be the full TOML dependency line, not just the version.

        Replacing bare "1" corrupts the first occurrence of that digit in the
        file (e.g. inside version = "0.15.0"), so we must use `name = "ver"`.
        """
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=0),
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{
            "name": "syn",
            "current_version": "1",
            "latest_version": "2.0.117",
            "manifest_path": "dotenv_codegen_implementation/Cargo.toml",
        }])
        tool.invoke({"folder_name": "main", "ecosystem": "rust", "packages_json": packages})

        first_cmd = backend.execute.call_args_list[0][0][0]
        assert 'syn = "1"' in first_cmd
        assert 'syn = "2.0.117"' in first_cmd

    def test_rust_minor_bump_stays_single_command(self) -> None:
        backend = MagicMock()
        backend.execute.return_value = ExecResult(output="", exit_code=0)
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{
            "name": "clap",
            "current_version": "4.5.0",
            "latest_version": "4.6.0",
        }])
        tool.invoke({"folder_name": "main", "ecosystem": "rust", "packages_json": packages})

        assert backend.execute.call_count == 1
        cmd = backend.execute.call_args_list[0][0][0]
        assert "cargo update" in cmd


class TestGoVersionPrefix:
    def test_go_version_with_v_prefix_does_not_produce_double_v(self) -> None:
        """Go module proxy returns versions with 'v' prefix (e.g. 'v1.9.1').

        update.py must strip any leading 'v' before building the go get command
        so it produces '@v1.9.1', not '@vv1.9.1'.
        """
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # go get
            ExecResult(output="", exit_code=0),  # go mod tidy
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{
            "name": "github.com/gin-gonic/gin",
            "latest_version": "v1.9.1",  # registry-prefixed version
        }])
        tool.invoke({
            "folder_name": "main",
            "ecosystem": "go",
            "packages_json": packages,
        })

        go_cmd = backend.execute.call_args_list[0][0][0]
        assert "go get github.com/gin-gonic/gin@v1.9.1" in go_cmd
        assert "vv" not in go_cmd


class TestGoSubModuleRouting:
    def test_go_uses_manifest_dir_when_provided(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # go get
            ExecResult(output="", exit_code=0),  # go mod tidy
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{
            "name": "github.com/some/dep",
            "latest_version": "1.5.0",
            "manifest_path": "integration/go.mod",
        }])
        tool.invoke({"folder_name": "main", "ecosystem": "go", "packages_json": packages})

        go_cmd = backend.execute.call_args_list[0][0][0]
        assert f"{DEFAULT_WORKSPACE}/main/integration" in go_cmd
        assert "go get github.com/some/dep@v1.5.0" in go_cmd

    def test_go_tidy_runs_in_manifest_dir(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # go get
            ExecResult(output="", exit_code=0),  # go mod tidy
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{
            "name": "github.com/some/dep",
            "latest_version": "1.5.0",
            "manifest_path": "integration/go.mod",
        }])
        tool.invoke({"folder_name": "main", "ecosystem": "go", "packages_json": packages})

        tidy_cmd = backend.execute.call_args_list[1][0][0]
        assert f"{DEFAULT_WORKSPACE}/main/integration" in tidy_cmd
        assert "go mod tidy" in tidy_cmd

    def test_go_tidy_in_root_without_manifest_path(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # go get
            ExecResult(output="", exit_code=0),  # go mod tidy
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "github.com/gin-gonic/gin", "latest_version": "1.9.1"}])
        tool.invoke({"folder_name": "main", "ecosystem": "go", "packages_json": packages})

        tidy_cmd = backend.execute.call_args_list[1][0][0]
        assert f"{DEFAULT_WORKSPACE}/main" in tidy_cmd
        assert "go mod tidy" in tidy_cmd
        assert f"{DEFAULT_WORKSPACE}/main/integration" not in tidy_cmd


class TestPythonUpdateCommand:
    def test_python_with_manifest_path_patches_manifest_after_pip(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),  # pip install
            ExecResult(output="", exit_code=0),  # manifest patch
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{
            "name": "requests",
            "current_version": "2.28.0",
            "latest_version": "2.31.0",
            "manifest_path": "requirements.txt",
        }])
        tool.invoke({"folder_name": "main", "ecosystem": "python", "packages_json": packages})

        assert backend.execute.call_count == 2
        pip_cmd = backend.execute.call_args_list[0][0][0]
        assert "pip install requests==2.31.0" in pip_cmd
        patch_cmd = backend.execute.call_args_list[1][0][0]
        assert "python3 -c" in patch_cmd

    def test_python_without_manifest_path_skips_manifest_patch(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{"name": "requests", "latest_version": "2.31.0"}])
        tool.invoke({"folder_name": "main", "ecosystem": "python", "packages_json": packages})

        assert backend.execute.call_count == 1

    def test_python_manifest_patch_targets_pip_line(self) -> None:
        """old_string must be name==version, not bare version.

        Replacing "2.28.0" alone could match any field containing that string;
        using "requests==2.28.0" is scoped to the actual pip requirement line.
        """
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
            ExecResult(output="", exit_code=0),
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{
            "name": "requests",
            "current_version": "2.28.0",
            "latest_version": "2.31.0",
            "manifest_path": "requirements.txt",
        }])
        tool.invoke({"folder_name": "main", "ecosystem": "python", "packages_json": packages})

        patch_cmd = backend.execute.call_args_list[1][0][0]
        assert "requests==2.28.0" in patch_cmd
        assert "requests==2.31.0" in patch_cmd

    def test_python_manifest_patch_skipped_on_pip_failure(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="ERROR: no matching version", exit_code=1),
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        packages = json.dumps([{
            "name": "requests",
            "current_version": "2.28.0",
            "latest_version": "2.31.0",
            "manifest_path": "requirements.txt",
        }])
        tool.invoke({"folder_name": "main", "ecosystem": "python", "packages_json": packages})

        assert backend.execute.call_count == 1


class TestUpdateJava:
    def test_maven_uses_mvn_versions_plugin(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)
        packages = json.dumps([{
            "name": "org.springframework.boot:spring-boot-starter",
            "latest_version": "3.3.0",
            "current_version": "3.2.0",
            "manifest_path": "pom.xml",
        }])
        tool.invoke({"folder_name": "main", "ecosystem": "java", "packages_json": packages})

        cmd = backend.execute.call_args_list[0][0][0]
        assert "mvn versions:use-dep-version" in cmd
        assert "3.3.0" in cmd
        assert "org.springframework.boot:spring-boot-starter" in cmd
        assert "generateBackupPoms=false" in cmd

    def test_gradle_patches_manifest(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)
        packages = json.dumps([{
            "name": "com.example:library",
            "latest_version": "2.0.0",
            "current_version": "1.0.0",
            "manifest_path": "build.gradle",
        }])
        tool.invoke({"folder_name": "main", "ecosystem": "java", "packages_json": packages})

        cmd = backend.execute.call_args_list[0][0][0]
        # python3 manifest patch command
        assert "python3" in cmd
        assert "com.example:library:1.0.0" in cmd
        assert "com.example:library:2.0.0" in cmd

    def test_gradle_skips_patch_without_current_version(self) -> None:
        backend = MagicMock()
        backend.execute.side_effect = [
            ExecResult(output="", exit_code=0),
        ]
        tool = create_update_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)
        packages = json.dumps([{
            "name": "com.example:library",
            "latest_version": "2.0.0",
            "manifest_path": "build.gradle",
        }])
        result = tool.invoke({"folder_name": "main", "ecosystem": "java", "packages_json": packages})

        # Should still attempt something and not crash
        assert "com.example:library" in result
