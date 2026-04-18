# SPDX-License-Identifier: Apache-2.0

"""Tests for detect_languages tool."""

import json

from migratowl.agent.tools.detect import create_detect_languages_tool
from migratowl.models.schemas import Ecosystem
from tests.conftest import make_backend

DEFAULT_WORKSPACE = "/home/user/workspace"
CUSTOM_WORKSPACE = "/opt/workspace"


class TestDetectLanguagesTool:
    def test_detects_single_python_project(self) -> None:
        backend = make_backend(
            output=f"{DEFAULT_WORKSPACE}/pyproject.toml\n"
        )
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({})
        detections = json.loads(result)

        assert len(detections) == 1
        assert detections[0]["ecosystem"] == Ecosystem.PYTHON
        assert detections[0]["marker_file"] == "pyproject.toml"
        assert detections[0]["project_root"] == "."

    def test_detects_multiple_ecosystems(self) -> None:
        find_output = (
            f"{DEFAULT_WORKSPACE}/backend/pyproject.toml\n"
            f"{DEFAULT_WORKSPACE}/frontend/package.json\n"
            f"{DEFAULT_WORKSPACE}/services/api/go.mod\n"
        )
        backend = make_backend(output=find_output)
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({})
        detections = json.loads(result)

        assert len(detections) == 3
        ecosystems = {d["ecosystem"] for d in detections}
        assert ecosystems == {Ecosystem.PYTHON, Ecosystem.NODEJS, Ecosystem.GO}

    def test_no_markers_found(self) -> None:
        backend = make_backend(output="")
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({})

        assert "no" in result.lower() or "not found" in result.lower()

    def test_find_command_excludes_noise_dirs(self) -> None:
        backend = make_backend(output="")
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        tool.invoke({})

        find_cmd = backend.execute.call_args[0][0]
        assert "node_modules" in find_cmd
        assert ".venv" in find_cmd
        assert ".git" in find_cmd

    def test_deduplicates_python_in_same_dir(self) -> None:
        find_output = (
            f"{DEFAULT_WORKSPACE}/pyproject.toml\n"
            f"{DEFAULT_WORKSPACE}/requirements.txt\n"
        )
        backend = make_backend(output=find_output)
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({})
        detections = json.loads(result)

        python_detections = [d for d in detections if d["ecosystem"] == Ecosystem.PYTHON]
        assert len(python_detections) == 1
        assert python_detections[0]["marker_file"] == "pyproject.toml"

    def test_python_markers_in_different_dirs(self) -> None:
        find_output = (
            f"{DEFAULT_WORKSPACE}/pyproject.toml\n"
            f"{DEFAULT_WORKSPACE}/subproject/requirements.txt\n"
        )
        backend = make_backend(output=find_output)
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({})
        detections = json.loads(result)

        assert len(detections) == 2
        roots = {d["project_root"] for d in detections}
        assert roots == {".", "subproject"}

    def test_custom_workspace_path(self) -> None:
        backend = make_backend(output=f"{CUSTOM_WORKSPACE}/package.json\n")
        tool = create_detect_languages_tool(lambda: backend, workspace_path=CUSTOM_WORKSPACE)

        tool.invoke({})

        find_cmd = backend.execute.call_args[0][0]
        assert CUSTOM_WORKSPACE in find_cmd

    def test_find_command_failure(self) -> None:
        backend = make_backend(output="find: permission denied", exit_code=1)
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({})

        assert "error" in result.lower() or "failed" in result.lower()

    def test_project_root_relative_to_workspace(self) -> None:
        find_output = f"{DEFAULT_WORKSPACE}/sub/dir/package.json\n"
        backend = make_backend(output=find_output)
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({})
        detections = json.loads(result)

        assert detections[0]["project_root"] == "sub/dir"

    def test_project_root_dot_for_workspace_root(self) -> None:
        find_output = f"{DEFAULT_WORKSPACE}/go.mod\n"
        backend = make_backend(output=find_output)
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({})
        detections = json.loads(result)

        assert detections[0]["project_root"] == "."

    def test_returns_valid_json(self) -> None:
        find_output = f"{DEFAULT_WORKSPACE}/pyproject.toml\n"
        backend = make_backend(output=find_output)
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({})
        parsed = json.loads(result)

        assert isinstance(parsed, list)
        assert all(isinstance(d, dict) for d in parsed)

    def test_default_commands_per_ecosystem(self) -> None:
        find_output = (
            f"{DEFAULT_WORKSPACE}/pyproject.toml\n"
            f"{DEFAULT_WORKSPACE}/frontend/package.json\n"
            f"{DEFAULT_WORKSPACE}/go-svc/go.mod\n"
            f"{DEFAULT_WORKSPACE}/rust-svc/Cargo.toml\n"
        )
        backend = make_backend(output=find_output)
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)

        result = tool.invoke({})
        detections = {d["ecosystem"]: d for d in json.loads(result)}

        assert detections[Ecosystem.PYTHON]["default_test_command"] == "pytest -x --tb=short"
        assert detections[Ecosystem.PYTHON]["default_install_command"] == "pip install -e ."
        assert detections[Ecosystem.NODEJS]["default_test_command"] == "npm test"
        assert detections[Ecosystem.NODEJS]["default_install_command"] == "npm install"
        assert detections[Ecosystem.GO]["default_test_command"] == "go test ./..."
        assert detections[Ecosystem.GO]["default_install_command"] == "go mod download"
        assert detections[Ecosystem.RUST]["default_test_command"] == "cargo test"
        assert detections[Ecosystem.RUST]["default_install_command"] == "cargo build"


class TestDetectJava:
    def test_detects_maven_pom_xml(self) -> None:
        backend = make_backend(output=f"{DEFAULT_WORKSPACE}/pom.xml\n")
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)
        result = tool.invoke({})
        detections = json.loads(result)

        assert len(detections) == 1
        assert detections[0]["ecosystem"] == Ecosystem.JAVA
        assert detections[0]["marker_file"] == "pom.xml"
        assert "mvn" in detections[0]["default_test_command"]
        assert "mvn" in detections[0]["default_install_command"]

    def test_detects_gradle_build_file(self) -> None:
        backend = make_backend(output=f"{DEFAULT_WORKSPACE}/build.gradle\n")
        tool = create_detect_languages_tool(lambda: backend, workspace_path=DEFAULT_WORKSPACE)
        result = tool.invoke({})
        detections = json.loads(result)

        assert len(detections) == 1
        assert detections[0]["ecosystem"] == Ecosystem.JAVA
        assert detections[0]["marker_file"] == "build.gradle"
        assert "gradle" in detections[0]["default_test_command"]