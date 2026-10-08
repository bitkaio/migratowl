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

"""Update dependencies tool for the Migratowl agent."""

import json
import os
import re
import shlex
from collections.abc import Callable
from typing import Any

from langchain.tools import tool


def create_update_dependencies_tool(
    get_backend: Callable[[], Any],
    workspace_path: str,
) -> Any:
    """Create an update_dependencies tool bound to a sandbox backend.

    Args:
        get_backend: Callable that returns a sandbox backend.
        workspace_path: Root workspace path inside the sandbox.
    """

    @tool
    def update_dependencies(
        folder_name: str,
        ecosystem: str,
        packages_json: str,
    ) -> str:
        """Update specific packages to their latest versions in a working folder.

        After this tool returns, call validate_project to build and run tests.

        Args:
            folder_name: Target folder name (e.g. "main", "requests").
            ecosystem: One of "python", "nodejs", "go", "rust", "java".
            packages_json: JSON array of objects with "name" and "latest_version".
                Optional fields: "current_version", "manifest_path".
        """
        backend = get_backend()
        folder_path = f"{workspace_path}/{folder_name}"
        packages = json.loads(packages_json)

        results: list[dict[str, Any]] = []
        has_failure = False
        go_tidy_dirs: set[str] = set()

        for pkg in packages:
            name = pkg["name"]
            version = pkg["latest_version"]
            current_version = pkg.get("current_version")
            manifest_rel = pkg.get("manifest_path")
            manifest_abs = (
                f"{workspace_path}/{folder_name}/{manifest_rel}"
                if manifest_rel else None
            )
            cmds = _build_update_cmd(
                ecosystem, name, version, folder_path,
                current_version=current_version,
                manifest_abs_path=manifest_abs,
                venv=venv_path(workspace_path, folder_name),
                module_path=pkg.get("module_path"),
                version_key=pkg.get("version_key"),
            )
            pkg_succeeded = True
            for cmd in cmds:
                result = backend.execute(cmd)
                results.append({
                    "package": name,
                    "version": version,
                    "exit_code": result.exit_code,
                    "output": result.output.strip(),
                })
                if result.exit_code != 0:
                    has_failure = True
                    pkg_succeeded = False
                    break  # skip manifest patch if pip/cargo step failed

            if ecosystem == "go" and pkg_succeeded:
                go_tidy_dirs.add(
                    os.path.dirname(manifest_abs) if manifest_abs else folder_path
                )

        # Go requires go mod tidy after go get to sync go.sum
        if ecosystem == "go":
            dirs_to_tidy = go_tidy_dirs if go_tidy_dirs else {folder_path}
            for tidy_dir in sorted(dirs_to_tidy):
                tidy = backend.execute(_sh(f"cd {q(tidy_dir)} && go mod tidy"))
                results.append({
                    "package": "(go mod tidy)",
                    "exit_code": tidy.exit_code,
                    "output": tidy.output.strip(),
                })
                if tidy.exit_code != 0:
                    has_failure = True

        summary_lines = []
        for r in results:
            status = "OK" if r["exit_code"] == 0 else f"FAILED (exit {r['exit_code']})"
            line = f"  {r['package']}: {status}"
            if r["exit_code"] != 0 and r["output"]:
                line += f" — {r['output'][:200]}"
            summary_lines.append(line)

        pkg_count = len(packages)
        header = f"Updated {pkg_count} package(s) in {folder_name}/"
        if has_failure:
            header = f"Errors updating packages in {folder_name}/"

        return header + "\n" + "\n".join(summary_lines)

    return update_dependencies


def _sh(cmd: str) -> str:
    """Wrap a command in ``sh -c`` so it runs through a shell.

    The sandbox backend executes commands directly (no shell), so builtins
    like ``cd`` and operators like ``&&`` require an explicit shell wrapper.

    The whole script is quoted as one argument, so quotes inside ``cmd``
    survive. Values interpolated into ``cmd`` must still go through ``q()``.

    Sets ``PIP_BREAK_SYSTEM_PACKAGES=1`` so pip works in PEP 668
    externally-managed containers (harmless for non-pip commands).
    """
    return "sh -c " + q(f"export PIP_BREAK_SYSTEM_PACKAGES=1 && {cmd}")


def q(value: str) -> str:
    """Quote one shell argument. Names, versions and paths may come from untrusted manifests."""
    return shlex.quote(value)


def venv_path(workspace_path: str, folder_name: str) -> str:
    """Python venv for one working folder.

    Every folder gets its own venv so a per-package run never sees packages
    another folder installed. It lives outside the project tree so test
    discovery and manifest scans never walk into it.
    """
    return f"{workspace_path}/.venvs/{folder_name}"


def activate_venv(venv: str) -> str:
    """Shell fragment that creates the venv on first use and activates it."""
    return f"(test -x {q(venv + '/bin/python')} || python3 -m venv {q(venv)}) && . {q(venv + '/bin/activate')}"


def reapply_pins(venv: str) -> str:
    """Shell fragment that re-installs the versions update_dependencies pinned.

    ``pip install -e .`` resolves the project's own constraints and can
    downgrade a bumped package (e.g. ``requests<3``); re-applying the pins
    afterwards keeps the bumped versions under test.
    """
    pins = q(f"{venv}/pins")
    return f"if [ -d {pins} ]; then cat {pins}/* | pip install -r /dev/stdin; fi"


def _is_major_bump(current: str, latest: str) -> bool:
    """Return True when latest has a higher major version than current.

    Strips leading semver operators (^~>=<) before comparing.
    Returns False when either string is empty or unparseable.
    """
    if not current or not latest:
        return False
    stripped_current = current.lstrip("^~>=<")
    stripped_latest = latest.lstrip("^~>=<")
    try:
        major_current = int(stripped_current.split(".")[0])
        major_latest = int(stripped_latest.split(".")[0])
    except (ValueError, IndexError):
        return False
    return major_latest > major_current


def _manifest_patch_cmd(
    manifest_abs_path: str,
    old_string: str,
    new_string: str,
) -> str:
    """Build a python3 command that patches a manifest via a one-liner.

    Calls python3 directly (no ``sh -c`` wrapper) to avoid nested single-quote
    breakage: ``_sh()`` wraps in ``sh -c '...'`` and ``shlex.quote()`` also
    produces ``'...'``, so the inner quotes close the outer ``sh -c`` context
    prematurely when the sandbox parses the command with ``shlex.split()``.
    """
    py_script = (
        'import sys; path,old,new=sys.argv[1:]; '
        'c=open(path).read(); open(path,"w").write(c.replace(old,new,1))'
    )
    return (
        f"python3 -c {shlex.quote(py_script)} "
        f"{shlex.quote(manifest_abs_path)} "
        f"{shlex.quote(old_string)} "
        f"{shlex.quote(new_string)}"
    )


def _build_rust_manifest_patch_cmd(
    manifest_abs_path: str,
    name: str,
    current_version: str,
    latest_version: str,
) -> str:
    """Patch a Rust Cargo.toml dependency using the full TOML line as old_string.

    Uses ``name = "current"`` instead of bare ``current`` to avoid matching the
    version string as a substring elsewhere in the file (e.g. in [package].version).
    Works for the simple string form (``syn = "1"``). For inline-table form
    (``clap = { version = "2", ... }``), the pattern will not match and the
    patch silently no-ops — cargo check then fails and the agent falls back to
    ``patch_manifest`` with the full table line.
    """
    old_string = f'{name} = "{current_version}"'
    new_string = f'{name} = "{latest_version}"'
    return _manifest_patch_cmd(manifest_abs_path, old_string, new_string)


def _build_python_manifest_patch_cmd(
    manifest_abs_path: str,
    name: str,
    current_version: str,
    latest_version: str,
) -> str:
    """Patch a Python requirements manifest using ``name==version`` as old_string.

    Uses the full pip requirement line instead of bare version to avoid matching
    the version string in unrelated fields (e.g. a comment or metadata entry).
    """
    old_string = f'{name}=={current_version}'
    new_string = f'{name}=={latest_version}'
    return _manifest_patch_cmd(manifest_abs_path, old_string, new_string)


_GO_IMPORT_REWRITE = (
    "import os, re, sys\n"
    "root, old, new = sys.argv[1:]\n"
    "pat = re.compile('\"' + re.escape(old) + '(?=[\"/])')\n"
    "for d, dirs, files in os.walk(root):\n"
    "    dirs[:] = [x for x in dirs if x not in ('vendor', '.git')]\n"
    "    for f in files:\n"
    "        if f.endswith('.go'):\n"
    "            p = os.path.join(d, f)\n"
    "            s = open(p, encoding='utf-8', errors='surrogateescape').read()\n"
    "            t = pat.sub('\"' + new, s)\n"
    "            if t != s:\n"
    "                open(p, 'w', encoding='utf-8', errors='surrogateescape').write(t)\n"
)


def _go_import_rewrite_cmd(root: str, old: str, new: str) -> str:
    """Rewrite Go imports of ``old`` (and its subpackages) to ``new`` under ``root``, skipping vendor/."""
    return f"python3 -c {shlex.quote(_GO_IMPORT_REWRITE)} {q(root)} {q(old)} {q(new)}"


_CATALOG_PATCH = r"""
import re, sys

path, module, key, old, new = sys.argv[1:]
group, _, artifact = module.partition(":")
Q = "[\"']"


def quoted(value):
    return Q + re.escape(value) + Q


if key:
    section = "versions"
    patterns = [r"^(\s*" + Q + "?" + re.escape(key) + Q + r"?\s*=\s*" + Q + ")VERSION(" + Q + ")"]
    owns = lambda line: True
else:
    section = "libraries"
    patterns = [
        "(" + Q + re.escape(module) + ":)VERSION(" + Q + ")",
        r"(\bversion\s*=\s*" + Q + ")VERSION(" + Q + ")",
    ]
    owns = lambda line: bool(re.search(Q + re.escape(module) + ":", line) or re.search(
        r"\bmodule\s*=\s*" + quoted(module), line) or (
        re.search(r"\bgroup\s*=\s*" + quoted(group), line)
        and re.search(r"\bname\s*=\s*" + quoted(artifact), line)))


def find(lines, version):
    current = ""
    for i, line in enumerate(lines):
        m = re.match(r"\s*\[([^\]]+)\]", line)
        if m:
            current = m.group(1).strip()
        elif current == section and owns(line):
            for pattern in patterns:
                pat = pattern.replace("VERSION", re.escape(version))
                if re.search(pat, line):
                    return i, pat
    return None, None


lines = open(path).read().splitlines(keepends=True)
i, pat = find(lines, old)
if i is None:
    if find(lines, new)[0] is not None:
        sys.exit(0)  # a shared [versions] key another library already bumped
    sys.exit("version " + old + " of " + module + " not found in " + path)
lines[i] = re.sub(pat, lambda m: m.group(1) + new + m.group(2), lines[i], count=1)
open(path, "w").write("".join(lines))
"""


def _catalog_patch_cmd(path: str, module: str, version_key: str | None, old: str, new: str) -> str:
    """Bump one entry of a Gradle version catalog: the ``[versions]`` key, or the library's own version.

    Line-based on purpose: TOML inline tables are always on one line, and rewriting through a
    TOML library would drop the file's comments and layout.
    """
    return (
        f"python3 -c {shlex.quote(_CATALOG_PATCH)} "
        f"{q(path)} {q(module)} {q(version_key or '')} {q(old)} {q(new)}"
    )


def _build_update_cmd(
    ecosystem: str,
    name: str,
    version: str,
    folder_path: str,
    *,
    current_version: str | None = None,
    manifest_abs_path: str | None = None,
    venv: str | None = None,
    module_path: str | None = None,
    version_key: str | None = None,
) -> list[str]:
    """Build the shell command(s) to update a single package.

    Returns a list of commands to execute in order. Execution stops on first
    non-zero exit code (the caller is responsible for the break).
    """
    if ecosystem == "python":
        venv = venv or venv_path(os.path.dirname(folder_path), os.path.basename(folder_path))
        pins = f"{venv}/pins"
        spec = f"{name}=={version}"
        pin_file = f"{pins}/{re.sub(r'[^A-Za-z0-9._-]', '_', name)}"
        cmds = [_sh(
            f"{activate_venv(venv)} && cd {q(folder_path)} && pip install {q(spec)} && "
            f"mkdir -p {q(pins)} && echo {q(spec)} > {q(pin_file)}"
        )]
        if current_version and manifest_abs_path:
            cmds.append(
                _build_python_manifest_patch_cmd(
                    manifest_abs_path, name, current_version, version
                )
            )
        return cmds
    elif ecosystem == "nodejs":
        return [_sh(f"cd {q(folder_path)} && npm install {q(f'{name}@{version}')}")]
    elif ecosystem == "go":
        run_dir = os.path.dirname(manifest_abs_path) if manifest_abs_path else folder_path
        clean_version = version.lstrip("v")
        # A new major lives at a new module path (github.com/x/y → github.com/x/y/v2):
        # require it and point the code's imports at it, or `go mod tidy` drops it again.
        target = module_path or name
        cmds = [_sh(f"cd {q(run_dir)} && go get {q(f'{target}@v{clean_version}')}")]
        if module_path and module_path != name:
            cmds.append(_go_import_rewrite_cmd(folder_path, name, module_path))
        return cmds
    elif ecosystem == "rust":
        if current_version and _is_major_bump(current_version, version) and manifest_abs_path:
            return [
                _build_rust_manifest_patch_cmd(
                    manifest_abs_path, name, current_version, version
                ),
                _sh(f"cd {q(folder_path)} && cargo check"),
            ]
        elif current_version:
            # Use only the major version as the @specifier so it matches the
            # lockfile-resolved version (e.g. tempfile@3 matches 3.27.0, whereas
            # tempfile@3.0.0 would fail when the lockfile has 3.27.0).
            major = current_version.lstrip("^~>=<").split(".")[0]
            return [_sh(f"cd {q(folder_path)} && cargo update -p {q(f'{name}@{major}')} --precise {q(version)}")]
        else:
            return [_sh(f"cd {q(folder_path)} && cargo update -p {q(name)} --precise {q(version)}")]
    elif ecosystem == "java":
        manifest_name = os.path.basename(manifest_abs_path) if manifest_abs_path else ""
        if manifest_abs_path and current_version and manifest_name == "libs.versions.toml":
            return [_catalog_patch_cmd(manifest_abs_path, name, version_key, current_version, version)]
        if manifest_abs_path and current_version and version_key and manifest_name == "pom.xml":
            # ${spring.version}: bump the property, which every dependency using it shares.
            return [_manifest_patch_cmd(
                manifest_abs_path,
                f"<{version_key}>{current_version}</{version_key}>",
                f"<{version_key}>{version}</{version_key}>",
            )]
        if manifest_abs_path and manifest_name == "pom.xml":
            group_id, artifact_id = name.split(":", 1) if ":" in name else (name, "")
            run_dir = os.path.dirname(manifest_abs_path)
            return [_sh(
                f"cd {q(run_dir)} && mvn versions:use-dep-version"
                f" -DdepVersion={q(version)}"
                f" -Dincludes={q(f'{group_id}:{artifact_id}')}"
                f" -DforceVersion=true"
                f" -DgenerateBackupPoms=false"
                f" -q"
            )]
        elif manifest_abs_path and current_version:
            # build.gradle: patch 'groupId:artifactId:old' → 'groupId:artifactId:new'
            return [_manifest_patch_cmd(
                manifest_abs_path,
                f"{name}:{current_version}",
                f"{name}:{version}",
            )]
        else:
            return ["echo " + q(f"Cannot update {name}: missing manifest path or current version")]
    else:
        return ["echo " + q(f"Unsupported ecosystem: {ecosystem}")]