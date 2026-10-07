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

"""Pure parsing functions for manifest files — text → list[Dependency]."""

import json
import re
import tomllib

import defusedxml.ElementTree as ET

from migratowl.models.schemas import Dependency, Ecosystem

# Operators that start a version constraint in requirements.txt
_REQ_OPERATORS = ("==", ">=", "<=", "~=", "!=", ">", "<")


def parse_requirements_txt(content: str, manifest_path: str) -> list[Dependency]:
    """Parse a pip requirements.txt file."""
    deps: list[Dependency] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-r", "-c", "-e", "--")) or "://" in line:
            continue

        # Strip inline comments
        comment_idx = line.find(" #")
        if comment_idx != -1:
            line = line[:comment_idx].strip()

        # Strip PEP 508 environment markers (`; python_version < "3.8"`)
        if ";" in line:
            line = line[: line.index(";")].strip()
        if not line:
            continue

        # Split name from version spec
        # Sort longest-first so ">=" is matched before ">" (avoids prefix collision)
        name = line
        version = ""
        for op in sorted(_REQ_OPERATORS, key=len, reverse=True):
            idx = line.find(op)
            if idx != -1:
                name = line[:idx].strip()
                version = line[idx:].strip()
                # For pinned versions (==), store just the version number
                if op == "==" and "," not in version:
                    version = version[2:]
                break

        deps.append(
            Dependency(
                name=name,
                current_version=version,
                ecosystem=Ecosystem.PYTHON,
                manifest_path=manifest_path,
            )
        )
    return deps


def _dict(value: object) -> dict:
    """``value`` if it is a table, else ``{}`` — manifests are untrusted input."""
    return value if isinstance(value, dict) else {}


def _table_version(value: object) -> str:
    """Version from a TOML dependency value: ``"1.0"`` or ``{ version = "1.0", ... }``."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        version = value.get("version", "")
        return version if isinstance(version, str) else ""
    return ""


def parse_pyproject_toml(content: str, manifest_path: str) -> list[Dependency]:
    """Parse a pyproject.toml file (PEP 621 / PEP 735 or Poetry).

    PEP 621 covers ``[project].dependencies`` and ``[project.optional-dependencies]``;
    PEP 735 ``[dependency-groups]`` entries are read too (``{include-group = ...}``
    entries are skipped). When ``[project].dependencies`` exists, Poetry tables are
    ignored; otherwise ``[tool.poetry.dependencies]`` and every
    ``[tool.poetry.group.<name>.dependencies]`` table are read.
    """
    if not content.strip():
        return []

    data = tomllib.loads(content)
    project = _dict(data.get("project"))

    specs: list[str] = []
    pep621_deps = project.get("dependencies")
    if isinstance(pep621_deps, list):
        specs += pep621_deps
        for group in _dict(project.get("optional-dependencies")).values():
            if isinstance(group, list):
                specs += group
    for group in _dict(data.get("dependency-groups")).values():
        if isinstance(group, list):
            specs += group

    deps: list[Dependency] = []
    for spec in specs:
        if not isinstance(spec, str):
            continue
        name, version = _parse_pep508(spec)
        deps.append(
            Dependency(
                name=name,
                current_version=version,
                ecosystem=Ecosystem.PYTHON,
                manifest_path=manifest_path,
            )
        )
    if isinstance(pep621_deps, list):
        return deps

    poetry = _dict(_dict(data.get("tool")).get("poetry"))
    tables = [_dict(poetry.get("dependencies"))] + [
        _dict(_dict(group).get("dependencies")) for group in _dict(poetry.get("group")).values()
    ]
    for table in tables:
        for pkg_name, value in table.items():
            if pkg_name.lower() == "python":
                continue
            deps.append(
                Dependency(
                    name=pkg_name,
                    current_version=_table_version(value),
                    ecosystem=Ecosystem.PYTHON,
                    manifest_path=manifest_path,
                )
            )

    return deps


def _parse_pep508(spec: str) -> tuple[str, str]:
    """Extract (name, version_constraint) from a PEP 508 string."""
    # Match name (with optional extras) then version operators
    m = re.match(r"^([A-Za-z0-9_.\-]+(?:\[[^\]]+\])?)\s*(.*)", spec)
    if not m:
        return spec, ""
    name = m.group(1)
    rest = m.group(2).strip()
    # Strip environment markers (after ;)
    if ";" in rest:
        rest = rest[: rest.index(";")].strip()
    return name, rest


def parse_package_json(content: str, manifest_path: str) -> list[Dependency]:
    """Parse a Node.js package.json file."""
    if not content.strip():
        return []

    data = json.loads(content)
    if not isinstance(data, dict):
        # Malformed manifest (top-level JSON is an int/str/list/null) — no deps.
        return []
    deps: list[Dependency] = []

    all_deps: dict[str, str] = {}
    deps_section = data.get("dependencies", {})
    dev_deps_section = data.get("devDependencies", {})
    if isinstance(deps_section, dict):
        all_deps.update(deps_section)
    if isinstance(dev_deps_section, dict):
        all_deps.update(dev_deps_section)

    for name, version_str in all_deps.items():
        # Version values are normally strings; a malformed manifest may supply
        # a non-string (e.g. {"express": 5}) — coerce defensively.
        if not isinstance(version_str, str):
            version_str = str(version_str)
        # Strip exactly one leading operator prefix; workspace: and bare versions are unaffected
        version = re.sub(r"^(?:\^|~|>=|<=|>|<|=)\s*", "", version_str, count=1)
        deps.append(
            Dependency(
                name=name,
                current_version=version,
                ecosystem=Ecosystem.NODEJS,
                manifest_path=manifest_path,
            )
        )

    return deps


def parse_go_mod(content: str, manifest_path: str) -> list[Dependency]:
    """Parse a Go go.mod file."""
    if not content.strip():
        return []

    deps: list[Dependency] = []

    # Single-line: require github.com/foo/bar v1.2.3
    for m in re.finditer(r"^require\s+(\S+)\s+(v\S+)", content, re.MULTILINE):
        deps.append(
            Dependency(
                name=m.group(1),
                current_version=m.group(2).lstrip("v"),
                ecosystem=Ecosystem.GO,
                manifest_path=manifest_path,
            )
        )

    # Block: require ( ... )
    for block in re.finditer(r"require\s*\((.*?)\)", content, re.DOTALL):
        for line_m in re.finditer(r"(\S+)\s+(v\S+)", block.group(1)):
            deps.append(
                Dependency(
                    name=line_m.group(1),
                    current_version=line_m.group(2).lstrip("v"),
                    ecosystem=Ecosystem.GO,
                    manifest_path=manifest_path,
                )
            )

    return deps


_CARGO_SECTIONS = ("dependencies", "dev-dependencies", "build-dependencies")


def parse_cargo_toml(content: str, manifest_path: str) -> list[Dependency]:
    """Parse a Rust Cargo.toml file.

    Reads ``[dependencies]``, ``[dev-dependencies]``, ``[build-dependencies]``,
    their ``[target.<cfg>.*]`` variants and ``[workspace.dependencies]``.
    """
    if not content.strip():
        return []

    data = tomllib.loads(content)
    tables = [_dict(data.get(section)) for section in _CARGO_SECTIONS]
    for target in _dict(data.get("target")).values():
        tables += [_dict(_dict(target).get(section)) for section in _CARGO_SECTIONS]
    tables.append(_dict(_dict(data.get("workspace")).get("dependencies")))

    return [
        Dependency(
            name=name,
            current_version=_table_version(value),
            ecosystem=Ecosystem.RUST,
            manifest_path=manifest_path,
        )
        for table in tables
        for name, value in table.items()
    ]


def parse_pom_xml(content: str, manifest_path: str) -> list[Dependency]:
    """Parse a Maven pom.xml file."""
    if not content.strip():
        return []

    root = ET.fromstring(content)
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    deps: list[Dependency] = []
    for dep in root.iter(f"{ns}dependency"):
        group_id = (dep.findtext(f"{ns}groupId") or "").strip()
        artifact_id = (dep.findtext(f"{ns}artifactId") or "").strip()
        version = (dep.findtext(f"{ns}version") or "").strip()
        if not group_id or not artifact_id:
            continue
        if not version or version.startswith("${"):
            continue
        deps.append(
            Dependency(
                name=f"{group_id}:{artifact_id}",
                current_version=version,
                ecosystem=Ecosystem.JAVA,
                manifest_path=manifest_path,
            )
        )
    return deps


def parse_build_gradle(content: str, manifest_path: str) -> list[Dependency]:
    """Parse a Gradle build.gradle file (string-form dependencies only)."""
    if not content.strip():
        return []

    deps: list[Dependency] = []
    # Matches: optional-quote  group:artifact:version  same-quote (or parenthesis-wrapped)
    pattern = re.compile(r"""(['"])([a-zA-Z0-9._\-]+:[a-zA-Z0-9._\-]+):([^'"\s]+)\1""")
    for m in pattern.finditer(content):
        deps.append(
            Dependency(
                name=m.group(2),
                current_version=m.group(3),
                ecosystem=Ecosystem.JAVA,
                manifest_path=manifest_path,
            )
        )
    return deps