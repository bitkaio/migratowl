"""Tool for fetching and filtering changelogs for outdated dependencies."""

import json
from typing import Any

from langchain.tools import tool

from migratowl.changelog import chunk_changelog_by_version, fetch_changelog, filter_chunks_by_version_range


def create_fetch_changelog_tool() -> Any:
    """Create a fetch_changelog tool for the agent."""

    @tool
    async def fetch_changelog_tool(outdated_dep_json: str) -> str:
        """Fetch and filter changelog for an outdated dependency.

        Takes JSON: {name, current_version, latest_version, changelog_url?, repository_url?}
        Returns JSON: {chunks: [{version, content}], warnings: []}
        """
        dep = json.loads(outdated_dep_json)
        text, warnings = await fetch_changelog(
            changelog_url=dep.get("changelog_url"),
            repository_url=dep.get("repository_url"),
            dep_name=dep["name"],
        )
        chunks = chunk_changelog_by_version(text)
        filtered = filter_chunks_by_version_range(
            chunks,
            dep["current_version"],
            dep["latest_version"],
        )
        return json.dumps({"chunks": filtered, "warnings": warnings})

    return fetch_changelog_tool
