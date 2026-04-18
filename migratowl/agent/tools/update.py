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
                tidy = backend.execute(_sh(f"cd {tidy_dir} && go mod tidy"))
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

    Sets ``PIP_BREAK_SYSTEM_PACKAGES=1`` so pip works in PEP 668
    externally-managed containers (harmless for non-pip commands).
    """
    return f"sh -c 'export PIP_BREAK_SYSTEM_PACKAGES=1 && {cmd}'"


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


def _build_update_cmd(
    ecosystem: str,
    name: str,
    version: str,
    folder_path: str,
    *,
    current_version: str | None = None,
    manifest_abs_path: str | None = None,
) -> list[str]:
    """Build the shell command(s) to update a single package.

    Returns a list of commands to execute in order. Execution stops on first
    non-zero exit code (the caller is responsible for the break).
    """
    if ecosystem == "python":
        cmds = [_sh(f"cd {folder_path} && pip install {name}=={version}")]
        if current_version and manifest_abs_path:
            cmds.append(
                _build_python_manifest_patch_cmd(
                    manifest_abs_path, name, current_version, version
                )
            )
        return cmds
    elif ecosystem == "nodejs":
        return [_sh(f"cd {folder_path} && npm install {name}@{version}")]
    elif ecosystem == "go":
        run_dir = os.path.dirname(manifest_abs_path) if manifest_abs_path else folder_path
        clean_version = version.lstrip("v")
        return [_sh(f"cd {run_dir} && go get {name}@v{clean_version}")]
    elif ecosystem == "rust":
        if current_version and _is_major_bump(current_version, version) and manifest_abs_path:
            return [
                _build_rust_manifest_patch_cmd(
                    manifest_abs_path, name, current_version, version
                ),
                _sh(f"cd {folder_path} && cargo check"),
            ]
        elif current_version:
            # Use only the major version as the @specifier so it matches the
            # lockfile-resolved version (e.g. tempfile@3 matches 3.27.0, whereas
            # tempfile@3.0.0 would fail when the lockfile has 3.27.0).
            major = current_version.lstrip("^~>=<").split(".")[0]
            return [_sh(f"cd {folder_path} && cargo update -p {name}@{major} --precise {version}")]
        else:
            return [_sh(f"cd {folder_path} && cargo update -p {name} --precise {version}")]
    elif ecosystem == "java":
        if manifest_abs_path and os.path.basename(manifest_abs_path) == "pom.xml":
            group_id, artifact_id = name.split(":", 1) if ":" in name else (name, "")
            run_dir = os.path.dirname(manifest_abs_path)
            return [_sh(
                f"cd {run_dir} && mvn versions:use-dep-version"
                f" -DdepVersion={version}"
                f" -Dincludes={group_id}:{artifact_id}"
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
            return [f"echo 'Cannot update {name}: missing manifest path or current version'"]
    else:
        return [f"echo 'Unsupported ecosystem: {ecosystem}'"]