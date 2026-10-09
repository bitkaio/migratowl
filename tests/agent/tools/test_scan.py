# SPDX-License-Identifier: Apache-2.0

"""Tests for scan_dependencies tool."""

import json
from unittest.mock import MagicMock

from migratowl.agent.tools.scan import create_scan_dependencies_tool
from migratowl.models.schemas import Ecosystem
from tests.conftest import ExecResult

DEFAULT_WORKSPACE = "/home/user/workspace"


def _make_backend_multi(responses: list[ExecResult]) -> MagicMock:
    """Create a mock backend that returns different results for successive execute calls."""
    backend = MagicMock()
    backend.execute.side_effect = responses
    return backend


class TestScanDependenciesTool:
    def test_single_requirements_txt(self) -> None:
        find_output = f"{DEFAULT_WORKSPACE}/requirements.txt\n"
        cat_output = "requests==2.31.0\nflask==3.0.0\n"
        backend = _make_backend_multi([
            ExecResult(output=find_output, exit_code=0),
            ExecResult(output=cat_output, exit_code=0),
        ])
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = json.loads(tool.invoke({}))

        assert len(result) == 2
        assert result[0]["name"] == "requests"
        assert result[0]["current_version"] == "2.31.0"
        assert result[0]["ecosystem"] == Ecosystem.PYTHON
        assert result[0]["manifest_path"] == "requirements.txt"

    def test_multiple_manifests(self) -> None:
        find_output = (
            f"{DEFAULT_WORKSPACE}/requirements.txt\n"
            f"{DEFAULT_WORKSPACE}/frontend/package.json\n"
        )
        backend = _make_backend_multi([
            ExecResult(output=find_output, exit_code=0),
            ExecResult(output="requests==1.0\n", exit_code=0),
            ExecResult(output='{"dependencies": {"express": "^4.18.0"}}', exit_code=0),
        ])
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = json.loads(tool.invoke({}))

        assert len(result) == 2
        ecosystems = {d["ecosystem"] for d in result}
        assert ecosystems == {Ecosystem.PYTHON, Ecosystem.NODEJS}

    def test_no_manifests_found(self) -> None:
        backend = _make_backend_multi([
            ExecResult(output="", exit_code=0),
        ])
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = json.loads(tool.invoke({}))

        assert result == []

    def test_find_command_failure(self) -> None:
        backend = _make_backend_multi([
            ExecResult(output="find: error", exit_code=1),
        ])
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({})

        assert "failed" in result.lower() or "error" in result.lower()

    def test_cat_failure_skips_manifest(self) -> None:
        find_output = (
            f"{DEFAULT_WORKSPACE}/requirements.txt\n"
            f"{DEFAULT_WORKSPACE}/package.json\n"
        )
        backend = _make_backend_multi([
            ExecResult(output=find_output, exit_code=0),
            ExecResult(output="cat: No such file", exit_code=1),  # requirements.txt fails
            ExecResult(output='{"dependencies": {"express": "^4.18.0"}}', exit_code=0),
        ])
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = json.loads(tool.invoke({}))

        assert len(result) == 1
        assert result[0]["name"] == "express"

    def test_manifest_path_relative_to_workspace(self) -> None:
        find_output = f"{DEFAULT_WORKSPACE}/sub/dir/requirements.txt\n"
        backend = _make_backend_multi([
            ExecResult(output=find_output, exit_code=0),
            ExecResult(output="requests==1.0\n", exit_code=0),
        ])
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = json.loads(tool.invoke({}))

        assert result[0]["manifest_path"] == "sub/dir/requirements.txt"

    def test_all_five_formats(self) -> None:
        find_output = (
            f"{DEFAULT_WORKSPACE}/requirements.txt\n"
            f"{DEFAULT_WORKSPACE}/pyproject.toml\n"
            f"{DEFAULT_WORKSPACE}/package.json\n"
            f"{DEFAULT_WORKSPACE}/go.mod\n"
            f"{DEFAULT_WORKSPACE}/Cargo.toml\n"
        )
        backend = _make_backend_multi([
            ExecResult(output=find_output, exit_code=0),
            ExecResult(output="requests==1.0\n", exit_code=0),
            ExecResult(output='[project]\ndependencies = ["flask>=2.0"]\n', exit_code=0),
            ExecResult(output='{"dependencies": {"express": "^4.0.0"}}', exit_code=0),
            ExecResult(output="module m\n\nrequire github.com/gin-gonic/gin v1.9.0\n", exit_code=0),
            ExecResult(output='[dependencies]\nserde = "1.0"\n', exit_code=0),
        ])
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = json.loads(tool.invoke({}))

        ecosystems = {d["ecosystem"] for d in result}
        assert Ecosystem.PYTHON in ecosystems
        assert Ecosystem.NODEJS in ecosystems
        assert Ecosystem.GO in ecosystems
        assert Ecosystem.RUST in ecosystems

    def test_returns_valid_json(self) -> None:
        backend = _make_backend_multi([
            ExecResult(output=f"{DEFAULT_WORKSPACE}/requirements.txt\n", exit_code=0),
            ExecResult(output="requests==1.0\n", exit_code=0),
        ])
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({})
        parsed = json.loads(result)

        assert isinstance(parsed, list)
        assert all(isinstance(d, dict) for d in parsed)

    def test_go_self_referential_dep_excluded(self) -> None:
        """Go deps matching any module declaration in the repo should be filtered out."""
        find_output = (
            f"{DEFAULT_WORKSPACE}/go.mod\n"
            f"{DEFAULT_WORKSPACE}/integration/go.mod\n"
        )
        root_go_mod = (
            "module github.com/Masterminds/squirrel\n\n"
            "require github.com/some/dep v1.0.0\n"
        )
        integration_go_mod = (
            "module github.com/Masterminds/squirrel/integration\n\n"
            "require (\n"
            "\tgithub.com/Masterminds/squirrel v1.5.0\n"
            "\tgithub.com/some/other v2.0.0\n"
            ")\n"
        )
        backend = _make_backend_multi([
            ExecResult(output=find_output, exit_code=0),
            ExecResult(output=root_go_mod, exit_code=0),
            ExecResult(output=integration_go_mod, exit_code=0),
        ])
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = json.loads(tool.invoke({}))

        names = [d["name"] for d in result]
        # Self-referential dep (root module name) should be excluded
        assert "github.com/Masterminds/squirrel" not in names
        # Sub-module self-ref should also be excluded
        assert "github.com/Masterminds/squirrel/integration" not in names
        # Real external deps should remain
        assert "github.com/some/dep" in names
        assert "github.com/some/other" in names


class TestScanJava:
    def test_scans_pom_xml(self) -> None:
        pom_content = """\
<project>
  <dependencies>
    <dependency>
      <groupId>com.example</groupId>
      <artifactId>library</artifactId>
      <version>1.0.0</version>
    </dependency>
  </dependencies>
</project>"""
        backend = _make_backend_multi([
            ExecResult(output=f"{DEFAULT_WORKSPACE}/pom.xml\n", exit_code=0),
            ExecResult(output=pom_content, exit_code=0),
        ])
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = json.loads(tool.invoke({}))

        assert len(result) == 1
        assert result[0]["name"] == "com.example:library"
        assert result[0]["ecosystem"] == "java"
        assert result[0]["manifest_path"] == "pom.xml"

    def test_scans_build_gradle(self) -> None:
        gradle_content = "dependencies {\n    implementation 'com.example:lib:1.0.0'\n}\n"
        backend = _make_backend_multi([
            ExecResult(output=f"{DEFAULT_WORKSPACE}/build.gradle\n", exit_code=0),
            ExecResult(output=gradle_content, exit_code=0),
        ])
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = json.loads(tool.invoke({}))

        assert len(result) == 1
        assert result[0]["ecosystem"] == "java"

class TestScanKotlinDsl:
    def test_scans_build_gradle_kts(self) -> None:
        kts = 'dependencies {\n    implementation("com.example:lib:1.0.0")\n}\n'
        backend = _make_backend_multi([
            ExecResult(output=f"{DEFAULT_WORKSPACE}/build.gradle.kts\n", exit_code=0),
            ExecResult(output=kts, exit_code=0),
        ])
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = json.loads(tool.invoke({}))

        assert [(d["name"], d["ecosystem"], d["manifest_path"]) for d in result] == [
            ("com.example:lib", "java", "build.gradle.kts")
        ]
        assert "-name 'build.gradle.kts'" in backend.execute.call_args_list[0][0][0]


class TestScanInstalledVersions:
    """Lockfiles next to (or above) a manifest give each dependency's installed version."""

    def _scan(self, files: dict[str, str]) -> dict[str, dict]:
        results = [ExecResult(output="\n".join(f"{DEFAULT_WORKSPACE}/{p}" for p in files) + "\n", exit_code=0)]
        results += [ExecResult(output=c, exit_code=0) for c in files.values()]
        backend = _make_backend_multi(results)
        tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)
        out = json.loads(tool.invoke({}))
        assert "-name 'package-lock.json'" in backend.execute.call_args_list[0][0][0]
        return {d["name"]: d for d in out}

    def test_npm_lock_in_same_dir(self) -> None:
        deps = self._scan({
            "package.json": '{"dependencies": {"express": "^4.18.0", "left-pad": "1.0.0"}}',
            "package-lock.json": '{"packages": {"node_modules/express": {"version": "4.21.2"}}}',
        })
        assert deps["express"]["current_version"] == "4.18.0"
        assert deps["express"]["installed_version"] == "4.21.2"
        assert deps["left-pad"]["installed_version"] is None

    def test_python_lock_with_normalized_names_and_extras(self) -> None:
        deps = self._scan({
            "pyproject.toml": '[project]\ndependencies = ["Requests[socks]>=2.0", "python_dateutil>=2"]\n',
            "uv.lock": '[[package]]\nname = "requests"\nversion = "2.31.0"\n\n'
                       '[[package]]\nname = "python-dateutil"\nversion = "2.9.0"\n',
        })
        assert deps["Requests[socks]"]["installed_version"] == "2.31.0"
        assert deps["python_dateutil"]["installed_version"] == "2.9.0"

    def test_cargo_workspace_lock_in_parent_dir(self) -> None:
        deps = self._scan({
            "Cargo.lock": '[[package]]\nname = "syn"\nversion = "1.0.109"\n\n'
                          '[[package]]\nname = "syn"\nversion = "2.0.48"\n',
            "crates/core/Cargo.toml": '[dependencies]\nsyn = "1.0"\n',
        })
        assert deps["syn"]["installed_version"] == "1.0.109"

    def test_yarn_lock_matches_the_declared_range(self) -> None:
        deps = self._scan({
            "package.json": '{"dependencies": {"debug": "^4.1.0"}}',
            "yarn.lock": 'debug@2.6.9:\n  version "2.6.9"\n\ndebug@^4.1.0:\n  version "4.3.4"\n',
        })
        assert deps["debug"]["installed_version"] == "4.3.4"

    def test_pnpm_lock(self) -> None:
        deps = self._scan({
            "package.json": '{"dependencies": {"lodash": "^4.17.0"}}',
            "pnpm-lock.yaml": "lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n"
                              "      lodash:\n        specifier: ^4.17.0\n        version: 4.17.21\n",
        })
        assert deps["lodash"]["installed_version"] == "4.17.21"

    def test_lock_of_another_ecosystem_is_ignored(self) -> None:
        deps = self._scan({
            "package.json": '{"dependencies": {"requests": "1.0.0"}}',
            "uv.lock": '[[package]]\nname = "requests"\nversion = "2.31.0"\n',
        })
        assert deps["requests"]["installed_version"] is None


def test_gradle_version_catalog_is_scanned() -> None:
    find_output = f"{DEFAULT_WORKSPACE}/gradle/libs.versions.toml\n"
    backend = _make_backend_multi([
        ExecResult(output=find_output, exit_code=0),
        ExecResult(output='[libraries]\nguava = "com.google.guava:guava:32.0.0-jre"\n', exit_code=0),
    ])
    tool = create_scan_dependencies_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

    result = json.loads(tool.invoke({}))

    assert "-name 'libs.versions.toml'" in backend.execute.call_args_list[0][0][0]
    assert result[0]["name"] == "com.google.guava:guava"
    assert result[0]["ecosystem"] == Ecosystem.JAVA
    assert result[0]["manifest_path"] == "gradle/libs.versions.toml"
