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
import yaml

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

    properties: dict[str, str] = {}
    props = root.find(f"{ns}properties")
    if props is not None:
        for prop in props:
            if isinstance(prop.tag, str):
                properties[prop.tag.removeprefix(ns)] = (prop.text or "").strip()

    deps: list[Dependency] = []
    for dep in root.iter(f"{ns}dependency"):
        group_id = (dep.findtext(f"{ns}groupId") or "").strip()
        artifact_id = (dep.findtext(f"{ns}artifactId") or "").strip()
        version = (dep.findtext(f"{ns}version") or "").strip()
        if not group_id or not artifact_id or not version:
            continue
        version, key = _resolve_pom_property(version, properties)
        if not version:
            continue
        deps.append(
            Dependency(
                name=f"{group_id}:{artifact_id}",
                current_version=version,
                ecosystem=Ecosystem.JAVA,
                manifest_path=manifest_path,
                version_key=key,
            )
        )
    return deps


_POM_PROPERTY = re.compile(r"^\$\{([^}]+)\}$")


def _resolve_pom_property(version: str, properties: dict[str, str]) -> tuple[str | None, str | None]:
    """Follow ``${name}`` through the pom's own <properties>: (literal version, property holding it).

    Returns ``(None, None)`` when the chain ends outside this pom (``${project.version}``, a parent's
    property) or loops, and ``(version, None)`` for a literal version.
    """
    key = None
    for _ in range(5):
        m = _POM_PROPERTY.match(version)
        if not m:
            return (None, None) if "${" in version else (version, key)
        key = m.group(1)
        if key not in properties:
            return None, None
        version = properties[key]
    return None, None


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

def parse_gradle_version_catalog(content: str, manifest_path: str) -> list[Dependency]:
    """Parse a Gradle version catalog (``gradle/libs.versions.toml``).

    Covers ``"group:artifact:version"`` strings, inline ``version = "x"`` and ``version.ref``
    into ``[versions]`` (recorded as ``version_key``). Rich versions (``strictly``, ``require``),
    unversioned (BOM-managed) entries and ``[plugins]`` are skipped.
    """
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return []
    versions = data.get("versions")
    libraries = data.get("libraries")
    if not isinstance(versions, dict):
        versions = {}
    if not isinstance(libraries, dict):
        return []

    deps: list[Dependency] = []
    for spec in libraries.values():
        key = None
        if isinstance(spec, str):
            parts = spec.split(":")
            if len(parts) != 3:
                continue
            name, version = f"{parts[0]}:{parts[1]}", parts[2]
        elif isinstance(spec, dict):
            module = spec.get("module")
            if not module and spec.get("group") and spec.get("name"):
                module = f"{spec['group']}:{spec['name']}"
            raw = spec.get("version")
            if isinstance(raw, dict) and isinstance(raw.get("ref"), str):
                key = raw["ref"]
                raw = versions.get(key)
            if not isinstance(module, str) or not isinstance(raw, str):
                continue
            name, version = module, raw
        else:
            continue
        deps.append(
            Dependency(
                name=name,
                current_version=version,
                ecosystem=Ecosystem.JAVA,
                manifest_path=manifest_path,
                version_key=key,
            )
        )
    return deps


# ---------------------------------------------------------------------------
# Lockfiles: name → installed version
# ---------------------------------------------------------------------------


def normalize_python_name(name: str) -> str:
    """PEP 503 normalized project name (``Foo_Bar.baz`` → ``foo-bar-baz``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_package_lock_json(content: str) -> dict[str, str]:
    """Top-level installed versions from npm's ``package-lock.json`` (v1, v2 and v3)."""
    try:
        data = json.loads(content)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    locked: dict[str, str] = {}
    packages = data.get("packages")
    if isinstance(packages, dict):
        for path, info in packages.items():
            # Only direct children of the root node_modules, not nested copies.
            if not isinstance(path, str) or not path.startswith("node_modules/") or not isinstance(info, dict):
                continue
            name = path.removeprefix("node_modules/")
            if "/node_modules/" in name:
                continue
            if isinstance(info.get("version"), str):
                locked[name] = info["version"]
        return locked
    dependencies = data.get("dependencies")
    if isinstance(dependencies, dict):
        for name, info in dependencies.items():
            if isinstance(info, dict) and isinstance(info.get("version"), str):
                locked[name] = info["version"]
    return locked


def _split_npm_descriptor(descriptor: str) -> tuple[str, str]:
    """``"@scope/pkg@^1.0"`` → ``("@scope/pkg", "^1.0")``; Yarn Berry's ``npm:`` protocol is dropped."""
    at = descriptor.find("@", 1)
    if at < 0:
        return descriptor, ""
    return descriptor[:at], descriptor[at + 1 :].removeprefix("npm:")


def _node_lock_index(entries: list[tuple[str, str, str]]) -> dict[str, str]:
    """``name@range`` → version for each ``(name, range, version)``, plus ``name`` → version
    where the lockfile holds only one version of that package (yarn and pnpm lock several)."""
    locked: dict[str, str] = {}
    by_name: dict[str, set[str]] = {}
    for name, spec, version in entries:
        if not name or not version:
            continue
        if spec:
            locked[f"{name}@{spec}"] = version
        by_name.setdefault(name, set()).add(version)
    for name, versions in by_name.items():
        if len(versions) == 1:
            locked[name] = versions.pop()
    return locked


_YARN_V1_VERSION = re.compile(r'^\s+version:?\s+"?([^"\s]+)"?\s*$')


def parse_yarn_lock(content: str) -> dict[str, str]:
    """Installed versions from ``yarn.lock``, classic (v1) or Berry (YAML), keyed as in ``_node_lock_index``."""
    entries: list[tuple[str, str, str]] = []
    try:
        if "__metadata:" in content:
            # BaseLoader keeps every scalar a string ("1.10" must not become 1.1).
            data = yaml.load(content, Loader=yaml.BaseLoader)  # noqa: S506 - BaseLoader builds no objects
            if not isinstance(data, dict):
                return {}
            blocks = [(key, value.get("version")) for key, value in data.items()
                      if key != "__metadata" and isinstance(value, dict)]
        else:
            blocks = []
            header: str | None = None
            for line in content.splitlines():
                if line and not line[0].isspace() and not line.startswith("#") and line.rstrip().endswith(":"):
                    header = line.rstrip()[:-1]
                    continue
                m = _YARN_V1_VERSION.match(line)
                if header is not None and m:
                    blocks.append((header, m.group(1)))
                    header = None
        for header, version in blocks:
            if not isinstance(header, str) or not isinstance(version, str):
                continue
            for descriptor in header.split(","):
                name, spec = _split_npm_descriptor(descriptor.strip().strip('"'))
                if ":" in spec:  # workspace:, patch:, file:, link:, git … — not a registry version
                    continue
                entries.append((name, spec, version))
    except Exception:
        return {}
    return _node_lock_index(entries)


_PNPM_DEP_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies")


def parse_pnpm_lock(content: str) -> dict[str, str]:
    """Installed versions from ``pnpm-lock.yaml`` (v5 to v9, workspaces included), keyed as in ``_node_lock_index``."""
    entries: list[tuple[str, str, str]] = []
    try:
        data = yaml.load(content, Loader=yaml.BaseLoader)  # noqa: S506 - BaseLoader builds no objects
        if not isinstance(data, dict):
            return {}
        importers = data.get("importers")
        projects = list(importers.values()) if isinstance(importers, dict) else [data]
        for project in projects:
            if not isinstance(project, dict):
                continue
            specifiers = project.get("specifiers")  # v5 keeps ranges in a separate map
            if not isinstance(specifiers, dict):
                specifiers = {}
            for section in _PNPM_DEP_SECTIONS:
                deps = project.get(section)
                if not isinstance(deps, dict):
                    continue
                for name, info in deps.items():
                    if isinstance(info, dict):
                        spec, version = info.get("specifier", ""), info.get("version")
                    else:
                        spec, version = specifiers.get(name, ""), info
                    if not isinstance(version, str) or not isinstance(spec, str):
                        continue
                    # Strip peer-dependency suffixes: "18.2.0(react@18.2.0)" (v6+), "18.2.0_react@18.2.0" (v5).
                    version = re.split(r"[(_]", version, maxsplit=1)[0]
                    if ":" in version or "/" in version:  # link:, file:, git and aliased deps
                        continue
                    entries.append((name, spec, version))
    except Exception:
        return {}
    return _node_lock_index(entries)


def _toml_packages(content: str) -> list[tuple[str, str]]:
    """``(name, version)`` pairs from a TOML lockfile's ``[[package]]`` array."""
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return []
    packages = data.get("package")
    if not isinstance(packages, list):
        return []
    return [
        (p["name"], p["version"])
        for p in packages
        if isinstance(p, dict) and isinstance(p.get("name"), str) and isinstance(p.get("version"), str)
    ]


def parse_python_lock(content: str) -> dict[str, str]:
    """Installed versions from ``poetry.lock`` or ``uv.lock`` (keys PEP 503 normalized)."""
    return {normalize_python_name(name): version for name, version in _toml_packages(content)}


def parse_cargo_lock(content: str) -> dict[str, list[str]]:
    """Every locked version per crate from ``Cargo.lock`` (a crate can be locked at several majors)."""
    locked: dict[str, list[str]] = {}
    for name, version in _toml_packages(content):
        locked.setdefault(name, []).append(version)
    return locked


def _cargo_compat_key(version: str) -> tuple[int, ...] | None:
    """Cargo caret compatibility class: major, or (0, minor) for 0.x versions."""
    m = re.match(r"\s*[\^~=]?\s*(\d+)(?:\.(\d+))?", version)
    if not m:
        return None
    major = int(m.group(1))
    if major == 0 and m.group(2) is not None:
        return (0, int(m.group(2)))
    return (major,)


def pick_cargo_locked(declared: str, candidates: list[str]) -> str | None:
    """The locked version that satisfies ``declared`` under Cargo's caret rules, if any."""
    if not candidates:
        return None
    if not declared.strip():
        return candidates[-1]
    want = _cargo_compat_key(declared)
    if want is None:
        return None
    for version in candidates:
        got = _cargo_compat_key(version)
        if got is not None and got[: len(want)] == want:
            return version
    return None
