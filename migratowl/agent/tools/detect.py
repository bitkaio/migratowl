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

"""Detect language ecosystems in a cloned repository."""

import json
import os
from collections.abc import Callable
from typing import Any

from langchain.tools import tool

from migratowl.models.schemas import Ecosystem, LanguageDetection

# Ordered by priority — first match for (project_root, ecosystem) wins.
_MARKER_MAP: list[tuple[str, Ecosystem, str, str]] = [
    ("pyproject.toml", Ecosystem.PYTHON, "pytest -x --tb=short", "pip install -e ."),
    ("requirements.txt", Ecosystem.PYTHON, "pytest -x --tb=short", "pip install -r requirements.txt"),
    ("package.json", Ecosystem.NODEJS, "npm test", "npm install"),
    ("go.mod", Ecosystem.GO, "go test ./...", "go mod download"),
    ("Cargo.toml", Ecosystem.RUST, "cargo test", "cargo build"),
    ("pom.xml", Ecosystem.JAVA, "mvn test", "mvn install -DskipTests -q"),
    ("build.gradle", Ecosystem.JAVA, "gradle test", "gradle build -x test"),
]

_NOISE_DIRS = ["node_modules", ".venv", ".git", "__pycache__", ".tox", ".mypy_cache"]


def create_detect_languages_tool(
    get_backend: Callable[[], Any],
    workspace_path: str,
) -> Any:
    """Create a detect_languages tool bound to a sandbox backend."""

    marker_names = [m[0] for m in _MARKER_MAP]
    name_clauses = " -o ".join(f"-name '{n}'" for n in marker_names)
    exclude_clauses = " ".join(
        f"-not -path '*/{d}/*'" for d in _NOISE_DIRS
    )

    find_cmd = (
        f"find {workspace_path} -maxdepth 5 "
        f"{exclude_clauses} "
        f"\\( {name_clauses} \\) -type f"
    )

    # Build priority lookup: marker filename → index (lower = higher priority)
    marker_priority = {m[0]: i for i, m in enumerate(_MARKER_MAP)}

    @tool
    def detect_languages() -> str:
        """Detect language ecosystems in the cloned repository.

        Scans the workspace for known marker files (pyproject.toml, package.json, etc.)
        and returns detected ecosystems with their project roots and default commands.
        """
        backend = get_backend()
        result = backend.execute(find_cmd)

        if result.exit_code != 0:
            return f"Failed to scan for language markers (exit code {result.exit_code}): {result.output}"

        lines = [line.strip() for line in result.output.strip().split("\n") if line.strip()]
        if not lines:
            return "No language ecosystem markers found in the workspace."

        # Sort by marker priority so higher-priority markers are processed first
        lines.sort(key=lambda p: marker_priority.get(os.path.basename(p), 999))

        seen: set[tuple[str, Ecosystem]] = set()
        detections: list[LanguageDetection] = []

        for filepath in lines:
            filename = os.path.basename(filepath)
            dirpath = os.path.dirname(filepath)
            rel_root = os.path.relpath(dirpath, workspace_path)
            if rel_root == "":
                rel_root = "."

            for marker_name, ecosystem, test_cmd, install_cmd in _MARKER_MAP:
                if filename != marker_name:
                    continue

                key = (rel_root, ecosystem)
                if key in seen:
                    break

                seen.add(key)
                detections.append(
                    LanguageDetection(
                        ecosystem=ecosystem,
                        marker_file=filename,
                        project_root=rel_root,
                        default_test_command=test_cmd,
                        default_install_command=install_cmd,
                    )
                )
                break

        return json.dumps([d.model_dump() for d in detections])

    return detect_languages