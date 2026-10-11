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

"""Tool for fetching and filtering changelogs for outdated dependencies."""

import json
from typing import Any

from langchain.tools import tool

from migratowl.changelog import (
    chunk_changelog_by_version,
    drop_link_lists,
    extract_breaking_changes,
    fetch_changelog,
    filter_chunks_by_version_range,
    first_releases,
    merge_duplicate_versions,
    order_for_breaking,
    truncate_chunks,
)
from migratowl.config import get_settings


def create_fetch_changelog_tool() -> Any:
    """Create a fetch_changelog tool for the agent."""
    settings = get_settings()

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
            current_version=dep.get("current_version"),
            latest_version=dep.get("latest_version"),
        )
        chunks = merge_duplicate_versions(chunk_changelog_by_version(text))
        filtered = drop_link_lists(filter_chunks_by_version_range(
            chunks,
            dep["current_version"],
            dep["latest_version"],
        ))
        # The first release of each crossed major carries its breaking changes; keep those
        # ahead of the newer minor/patch notes so truncation drops the latter first.
        majors = {c["version"] for c in first_releases(filtered, dep["current_version"], dep["latest_version"])}
        extracted = order_for_breaking(
            extract_breaking_changes(filtered, always_keep=majors), dep["current_version"], dep["latest_version"]
        )
        capped, truncated = truncate_chunks(extracted, settings.max_changelog_chars)
        warnings = list(warnings)
        if truncated:
            warnings.append("Changelog truncated to fit context budget")
        return json.dumps({"chunks": capped, "warnings": warnings})

    return fetch_changelog_tool