"""Update dependencies tool for the MigratOwl agent."""

import json
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

        After this tool returns, call execute_project to install and run tests.

        Args:
            folder_name: Target folder name (e.g. "main", "requests").
            ecosystem: One of "python", "nodejs", "go", "rust".
            packages_json: JSON array of objects with "name" and "latest_version".
        """
        backend = get_backend()
        folder_path = f"{workspace_path}/{folder_name}"
        packages = json.loads(packages_json)

        results: list[dict[str, Any]] = []
        has_failure = False

        for pkg in packages:
            name = pkg["name"]
            version = pkg["latest_version"]
            cmd = _build_update_cmd(ecosystem, name, version, folder_path)
            result = backend.execute(cmd)
            entry = {
                "package": name,
                "version": version,
                "exit_code": result.exit_code,
                "output": result.output.strip(),
            }
            results.append(entry)
            if result.exit_code != 0:
                has_failure = True

        # Go requires go mod tidy after go get to sync go.sum
        if ecosystem == "go":
            tidy = backend.execute(_sh(f"cd {folder_path} && go mod tidy"))
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


def _build_update_cmd(ecosystem: str, name: str, version: str, folder_path: str) -> str:
    """Build the shell command to update a single package."""
    if ecosystem == "python":
        return _sh(f"cd {folder_path} && pip install {name}=={version}")
    elif ecosystem == "nodejs":
        return _sh(f"cd {folder_path} && npm install {name}@{version}")
    elif ecosystem == "go":
        return _sh(f"cd {folder_path} && go get {name}@v{version}")
    elif ecosystem == "rust":
        return _sh(f"cd {folder_path} && cargo update -p {name} --precise {version}")
    else:
        return f"echo 'Unsupported ecosystem: {ecosystem}'"
