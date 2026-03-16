"""Tool for checking outdated dependencies via package registries."""

import json
from typing import Any

from langchain.tools import tool

from migratowl.models.schemas import Dependency
from migratowl.registry import check_outdated


def create_check_outdated_tool(concurrency: int = 10) -> Any:
    """Create a check_outdated_deps tool with the given concurrency limit."""

    @tool
    async def check_outdated_deps(dependencies_json: str) -> str:
        """Check which dependencies are outdated by querying package registries.

        Takes a JSON array of dependency objects (output from scan_dependencies)
        and returns a JSON array of outdated dependencies with latest versions
        and metadata (homepage, repository, changelog URLs).
        """
        deps = [Dependency(**d) for d in json.loads(dependencies_json)]
        outdated = await check_outdated(deps, concurrency=concurrency)
        return json.dumps([d.model_dump() for d in outdated])

    return check_outdated_deps
