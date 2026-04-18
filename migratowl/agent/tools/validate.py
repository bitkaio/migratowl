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

"""Validate project tool — ecosystem-aware build and test runner."""

import json
import shlex
from collections.abc import Callable
from typing import Any

from langchain.tools import tool

from migratowl.agent.tools.update import _sh


def create_validate_project_tool(
    get_backend: Callable[[], Any],
    workspace_path: str,
    max_output_chars: int = 50_000,
) -> Any:
    """Create a validate_project tool bound to a sandbox backend.

    Args:
        get_backend: Callable that returns a sandbox backend.
        workspace_path: Root workspace path inside the sandbox.
        max_output_chars: Maximum characters to keep from command output.
    """

    @tool
    def validate_project(folder_name: str, ecosystem: str) -> str:
        """Build and test a project after dependency updates.

        Runs an ecosystem-appropriate build step first (catches API-breaking
        changes introduced by dep updates), then automatically detects and
        runs tests if a test suite is present. No guessing required — the
        right validation strategy is selected based on the ecosystem.

        Compiled ecosystems (go, rust): compilation is always attempted first.
        A build failure means the dependency update broke the API surface.
        Tests are only run when test files / #[test] functions are detected.

        Interpreted ecosystems (python, nodejs): installs dependencies first,
        then runs the detected test runner. For Node.js, TypeScript projects
        get an additional ``tsc --noEmit`` type-check before tests.

        Args:
            folder_name: Target folder name (e.g. "main", "requests").
            ecosystem: One of "python", "nodejs", "go", "rust", "java".
        """
        backend = get_backend()
        folder_path = f"{workspace_path}/{folder_name}"

        if ecosystem == "go":
            result = _validate_go(backend, folder_path, max_output_chars)
        elif ecosystem == "rust":
            result = _validate_rust(backend, folder_path, max_output_chars)
        elif ecosystem == "python":
            result = _validate_python(backend, folder_path, max_output_chars)
        elif ecosystem == "nodejs":
            result = _validate_nodejs(backend, folder_path, max_output_chars)
        elif ecosystem == "java":
            result = _validate_java(backend, folder_path, max_output_chars)
        else:
            return json.dumps({"error": f"Unsupported ecosystem: {ecosystem}"})

        return json.dumps(result)

    return validate_project


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _step(name: str, command: str, result: Any, max_chars: int) -> dict[str, Any]:
    output = result.output.strip()
    truncated = len(output) > max_chars
    return {
        "name": name,
        "command": command,
        "exit_code": result.exit_code,
        "output": output[:max_chars] if truncated else output,
        "truncated": truncated,
    }


def _skipped(name: str, reason: str) -> dict[str, Any]:
    return {"name": name, "skipped": True, "reason": reason}


def _is_passing(steps: list[dict[str, Any]]) -> bool:
    return all(s.get("exit_code", 0) == 0 for s in steps if "exit_code" in s)


# ---------------------------------------------------------------------------
# Per-ecosystem validators
# ---------------------------------------------------------------------------


def _validate_go(backend: Any, folder_path: str, max_chars: int) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    # Step 1: Build — catches API-breaking dep changes at compile time
    build_r = backend.execute(_sh(f"cd {folder_path} && go build ./..."))
    steps.append(_step("build", "go build ./...", build_r, max_chars))
    if build_r.exit_code != 0:
        return {"steps": steps, "passed": False}

    # Step 2: Detect test files
    detect_r = backend.execute(_sh(f'find {folder_path} -name "*_test.go" -maxdepth 5 | head -1'))
    if not detect_r.output.strip():
        steps.append(_skipped("test", "no *_test.go files found"))
        return {"steps": steps, "passed": True}

    # Step 3: Run tests
    test_r = backend.execute(_sh(f"cd {folder_path} && go test ./..."))
    steps.append(_step("test", "go test ./...", test_r, max_chars))
    return {"steps": steps, "passed": _is_passing(steps)}


def _validate_rust(backend: Any, folder_path: str, max_chars: int) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    # Step 1: Build — catches API-breaking dep changes at compile time
    build_r = backend.execute(_sh(f"cd {folder_path} && cargo build"))
    steps.append(_step("build", "cargo build", build_r, max_chars))
    if build_r.exit_code != 0:
        return {"steps": steps, "passed": False}

    # Step 2: Detect #[test] functions in Rust source files
    detect_r = backend.execute(_sh(
        f'grep -rl "#\\[test\\]" {folder_path} --include="*.rs" 2>/dev/null | head -1'
    ))
    if not detect_r.output.strip():
        steps.append(_skipped("test", "no #[test] functions found"))
        return {"steps": steps, "passed": True}

    # Step 3: Run tests
    test_r = backend.execute(_sh(f"cd {folder_path} && cargo test"))
    steps.append(_step("test", "cargo test", test_r, max_chars))
    return {"steps": steps, "passed": _is_passing(steps)}


def _validate_python(backend: Any, folder_path: str, max_chars: int) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    # Step 1: Install — try test extras first, fall back to bare install, then requirements.txt
    install_cmd = (
        f"cd {folder_path} && "
        f"pip install -e '.[tests]' 2>/dev/null || "
        f"pip install -e '.[test]' 2>/dev/null || "
        f"pip install -e . 2>/dev/null || "
        f"pip install -r requirements.txt"
    )
    install_r = backend.execute(_sh(install_cmd))
    steps.append(_step(
        "install",
        "pip install -e '.[tests]' || pip install -e '.[test]' || pip install -e . || pip install -r requirements.txt",
        install_r,
        max_chars,
    ))
    if install_r.exit_code != 0:
        return {"steps": steps, "passed": False}

    # Step 2: Detect pytest (config file or tests/ directory)
    detect_r = backend.execute(_sh(
        f"test -f {folder_path}/pytest.ini || "
        f"test -f {folder_path}/conftest.py || "
        f"test -d {folder_path}/tests"
    ))
    if detect_r.exit_code != 0:
        steps.append(_skipped("test", "no pytest configuration or tests/ directory found"))
        return {"steps": steps, "passed": True}

    # Step 3: Run pytest
    test_r = backend.execute(_sh(f"cd {folder_path} && python3 -m pytest -x --tb=short"))
    steps.append(_step("test", "python3 -m pytest -x --tb=short", test_r, max_chars))
    return {"steps": steps, "passed": _is_passing(steps)}


def _validate_nodejs(backend: Any, folder_path: str, max_chars: int) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    # Step 1: Install
    install_r = backend.execute(_sh(f"cd {folder_path} && npm install"))
    steps.append(_step("install", "npm install", install_r, max_chars))
    if install_r.exit_code != 0:
        return {"steps": steps, "passed": False}

    # Step 2: TypeScript check (if tsconfig.json present)
    ts_detect_r = backend.execute(_sh(f"test -f {folder_path}/tsconfig.json"))
    if ts_detect_r.exit_code == 0:
        tsc_r = backend.execute(_sh(f"cd {folder_path} && npx tsc --noEmit"))
        steps.append(_step("typescript", "tsc --noEmit", tsc_r, max_chars))
        if tsc_r.exit_code != 0:
            return {"steps": steps, "passed": False}

    # Step 3: Run npm test if a test script is defined in package.json
    pkg_json = f"{folder_path}/package.json"
    detect_script = (
        "import json,sys; "
        "p=json.load(open(sys.argv[1])); "
        "sys.exit(0 if p.get('scripts',{}).get('test') else 1)"
    )
    test_detect_r = backend.execute(
        f"python3 -c {shlex.quote(detect_script)} {shlex.quote(pkg_json)}"
    )
    if test_detect_r.exit_code != 0:
        steps.append(_skipped("test", "no test script in package.json"))
        return {"steps": steps, "passed": _is_passing(steps)}

    test_r = backend.execute(_sh(f"cd {folder_path} && npm test"))
    steps.append(_step("test", "npm test", test_r, max_chars))
    return {"steps": steps, "passed": _is_passing(steps)}


def _validate_java(backend: Any, folder_path: str, max_chars: int) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    # Detect build system: Maven (pom.xml) takes priority over Gradle
    has_pom = backend.execute(_sh(f"test -f {folder_path}/pom.xml"))
    use_maven = has_pom.exit_code == 0

    if use_maven:
        build_cmd = "mvn compile -q"
        test_cmd = "mvn test"
    else:
        build_cmd = "gradle compileJava -q"
        test_cmd = "gradle test"

    # Step 1: Compile — catches API-breaking dep changes
    build_r = backend.execute(_sh(f"cd {folder_path} && {build_cmd}"))
    steps.append(_step("build", build_cmd, build_r, max_chars))
    if build_r.exit_code != 0:
        return {"steps": steps, "passed": False}

    # Step 2: Detect test sources (src/test is the Maven/Gradle standard layout)
    detect_r = backend.execute(_sh(
        f'find {folder_path}/src/test -name "*.java" -maxdepth 5 2>/dev/null | head -1'
    ))
    if not detect_r.output.strip():
        steps.append(_skipped("test", "no Java test sources found in src/test"))
        return {"steps": steps, "passed": True}

    # Step 3: Run tests
    test_r = backend.execute(_sh(f"cd {folder_path} && {test_cmd}"))
    steps.append(_step("test", test_cmd, test_r, max_chars))
    return {"steps": steps, "passed": _is_passing(steps)}
