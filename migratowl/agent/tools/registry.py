"""Tool for checking outdated dependencies via package registries."""

import json
from typing import Any

from langchain.tools import tool

from migratowl.config import get_settings
from migratowl.models.schemas import Dependency, OutdatedDependency
from migratowl.registry import CheckOptions, check_outdated


def _major_version_gap(dep: OutdatedDependency) -> int:
    """Return the major version gap between current and latest versions."""
    try:
        current_major = int(dep.current_version.split(".")[0])
        latest_major = int(dep.latest_version.split(".")[0])
        return latest_major - current_major
    except (ValueError, IndexError):
        return 0


def create_check_outdated_tool(
    concurrency: int = 10,
    options: CheckOptions | None = None,
) -> Any:
    """Create a check_outdated_deps tool with the given concurrency limit and check options."""
    _options = options if options is not None else CheckOptions()

    @tool
    async def check_outdated_deps(dependencies_json: str) -> str:
        """Check which dependencies are outdated by querying package registries.

        Takes a JSON array of dependency objects (output from scan_dependencies)
        and returns a JSON object with:
          - "outdated": list of outdated dependencies with latest versions and metadata
          - "warning": null, or a message if the list was capped to the largest version gaps
        """
        deps = [Dependency(**d) for d in json.loads(dependencies_json)]
        outdated = await check_outdated(deps, options=_options, concurrency=concurrency)
        settings = get_settings()
        if len(outdated) > settings.max_outdated_deps:
            outdated = sorted(outdated, key=_major_version_gap, reverse=True)[: settings.max_outdated_deps]
            result = {
                "outdated": [d.model_dump() for d in outdated],
                "warning": f"Capped at {settings.max_outdated_deps} deps (largest version gaps shown first)",
            }
        else:
            result = {"outdated": [d.model_dump() for d in outdated], "warning": None}
        return json.dumps(result)

    return check_outdated_deps
