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

"""Scan manifest files to extract declared dependencies."""

import json
import os
import re
from collections.abc import Callable
from typing import Any

from langchain.tools import tool

from migratowl.models.schemas import Dependency, Ecosystem
from migratowl.parsers import (
    parse_build_gradle,
    parse_cargo_toml,
    parse_go_mod,
    parse_package_json,
    parse_pom_xml,
    parse_pyproject_toml,
    parse_requirements_txt,
)

# Map manifest filenames to (parser_function, ecosystem).
_MANIFEST_PARSERS: dict[str, tuple[Callable[[str, str], list[Dependency]], Ecosystem]] = {
    "requirements.txt": (parse_requirements_txt, Ecosystem.PYTHON),
    "pyproject.toml": (parse_pyproject_toml, Ecosystem.PYTHON),
    "package.json": (parse_package_json, Ecosystem.NODEJS),
    "go.mod": (parse_go_mod, Ecosystem.GO),
    "Cargo.toml": (parse_cargo_toml, Ecosystem.RUST),
    "pom.xml": (parse_pom_xml, Ecosystem.JAVA),
    "build.gradle": (parse_build_gradle, Ecosystem.JAVA),
}

_NOISE_DIRS = ["node_modules", ".venv", ".git", "__pycache__", ".tox", ".mypy_cache"]


def _extract_go_module_name(content: str) -> str | None:
    """Return the module path declared in a go.mod file, or None."""
    m = re.search(r"^module\s+(\S+)", content, re.MULTILINE)
    return m.group(1) if m else None


def create_scan_dependencies_tool(
    get_backend: Callable[[], Any],
    workspace_path: str,
) -> Any:
    """Create a scan_dependencies tool bound to a sandbox backend."""

    manifest_names = list(_MANIFEST_PARSERS.keys())
    name_clauses = " -o ".join(f"-name '{n}'" for n in manifest_names)
    exclude_clauses = " ".join(f"-not -path '*/{d}/*'" for d in _NOISE_DIRS)

    find_cmd = (
        f"find {workspace_path} -maxdepth 5 "
        f"{exclude_clauses} "
        f"\\( {name_clauses} \\) -type f"
    )

    @tool
    def scan_dependencies() -> str:
        """Scan manifest files in the workspace and extract all declared dependencies.

        Reads requirements.txt, pyproject.toml, package.json, go.mod, Cargo.toml,
        pom.xml, and build.gradle files, parses them, and returns a JSON array of
        dependency objects with name, current_version, ecosystem, and manifest_path.
        """
        backend = get_backend()
        result = backend.execute(find_cmd)

        if result.exit_code != 0:
            return f"Failed to scan for manifest files (exit code {result.exit_code}): {result.output}"

        lines = [line.strip() for line in result.output.strip().split("\n") if line.strip()]
        if not lines:
            return json.dumps([])

        all_deps: list[Dependency] = []
        go_module_names: set[str] = set()

        for filepath in lines:
            filename = os.path.basename(filepath)
            parser_entry = _MANIFEST_PARSERS.get(filename)
            if parser_entry is None:
                continue

            parser_fn, _ecosystem = parser_entry

            cat_result = backend.execute(f"cat {filepath}")
            if cat_result.exit_code != 0:
                continue

            if filename == "go.mod":
                module_name = _extract_go_module_name(cat_result.output)
                if module_name:
                    go_module_names.add(module_name)

            rel_path = os.path.relpath(filepath, workspace_path)
            deps = parser_fn(cat_result.output, rel_path)
            all_deps.extend(deps)

        if go_module_names:
            all_deps = [
                d for d in all_deps
                if not (d.ecosystem == Ecosystem.GO and d.name in go_module_names)
            ]

        return json.dumps([d.model_dump() for d in all_deps])

    return scan_dependencies