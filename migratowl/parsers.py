# SPDX-License-Identifier: Apache-2.0

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


def parse_pyproject_toml(content: str, manifest_path: str) -> list[Dependency]:
    """Parse a pyproject.toml file (PEP 621 or Poetry)."""
    if not content.strip():
        return []

    data = tomllib.loads(content)
    deps: list[Dependency] = []

    # PEP 621: [project].dependencies
    pep621_deps = data.get("project", {}).get("dependencies")
    if pep621_deps is not None:
        for spec in pep621_deps:
            name, version = _parse_pep508(spec)
            deps.append(
                Dependency(
                    name=name,
                    current_version=version,
                    ecosystem=Ecosystem.PYTHON,
                    manifest_path=manifest_path,
                )
            )
        return deps

    # Poetry: [tool.poetry.dependencies]
    poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for pkg_name, value in poetry_deps.items():
        if pkg_name.lower() == "python":
            continue
        if isinstance(value, str):
            version = value
        elif isinstance(value, dict):
            version = value.get("version", "")
        else:
            version = ""
        deps.append(
            Dependency(
                name=pkg_name,
                current_version=version,
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
    deps: list[Dependency] = []

    all_deps: dict[str, str] = {}
    all_deps.update(data.get("dependencies", {}))
    all_deps.update(data.get("devDependencies", {}))

    for name, version_str in all_deps.items():
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


def parse_cargo_toml(content: str, manifest_path: str) -> list[Dependency]:
    """Parse a Rust Cargo.toml file."""
    if not content.strip():
        return []

    data = tomllib.loads(content)
    deps: list[Dependency] = []

    for section in ("dependencies", "dev-dependencies"):
        for name, value in data.get(section, {}).items():
            if isinstance(value, str):
                version = value
            elif isinstance(value, dict):
                version = value.get("version", "")
            else:
                version = ""
            deps.append(
                Dependency(
                    name=name,
                    current_version=version,
                    ecosystem=Ecosystem.RUST,
                    manifest_path=manifest_path,
                )
            )

    return deps


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