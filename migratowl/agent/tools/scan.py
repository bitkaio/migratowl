"""Scan manifest files to extract declared dependencies."""

import json
import os
from collections.abc import Callable
from typing import Any

from langchain.tools import tool

from migratowl.models.schemas import Dependency, Ecosystem
from migratowl.parsers import (
    parse_cargo_toml,
    parse_go_mod,
    parse_package_json,
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
}

_NOISE_DIRS = ["node_modules", ".venv", ".git", "__pycache__", ".tox", ".mypy_cache"]


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

        Reads requirements.txt, pyproject.toml, package.json, go.mod, and Cargo.toml
        files, parses them, and returns a JSON array of dependency objects with name,
        current_version, ecosystem, and manifest_path.
        """
        backend = get_backend()
        result = backend.execute(find_cmd)

        if result.exit_code != 0:
            return f"Failed to scan for manifest files (exit code {result.exit_code}): {result.output}"

        lines = [line.strip() for line in result.output.strip().split("\n") if line.strip()]
        if not lines:
            return json.dumps([])

        all_deps: list[Dependency] = []

        for filepath in lines:
            filename = os.path.basename(filepath)
            parser_entry = _MANIFEST_PARSERS.get(filename)
            if parser_entry is None:
                continue

            parser_fn, _ecosystem = parser_entry

            cat_result = backend.execute(f"cat {filepath}")
            if cat_result.exit_code != 0:
                continue

            rel_path = os.path.relpath(filepath, workspace_path)
            deps = parser_fn(cat_result.output, rel_path)
            all_deps.extend(deps)

        return json.dumps([d.model_dump() for d in all_deps])

    return scan_dependencies
