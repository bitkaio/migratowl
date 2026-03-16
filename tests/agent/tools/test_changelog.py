"""Tests for fetch_changelog_tool agent tool."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from migratowl.agent.tools.changelog import create_fetch_changelog_tool


class TestFetchChangelogTool:
    async def test_fetches_and_filters_changelog(self) -> None:
        input_json = json.dumps({
            "name": "requests",
            "current_version": "2.28.0",
            "latest_version": "2.32.0",
            "changelog_url": "https://example.com/CHANGELOG.md",
            "repository_url": "https://github.com/psf/requests",
        })
        changelog_text = "## 2.32.0\nNew feature\n## 2.30.0\nBug fix\n## 2.28.0\nOld\n"

        with patch(
            "migratowl.agent.tools.changelog.fetch_changelog",
            new_callable=AsyncMock,
            return_value=(changelog_text, []),
        ):
            tool = create_fetch_changelog_tool()
            result = json.loads(await tool.ainvoke({"outdated_dep_json": input_json}))

        assert len(result["chunks"]) == 2
        versions = [c["version"] for c in result["chunks"]]
        assert "2.32.0" in versions
        assert "2.30.0" in versions
        assert "2.28.0" not in versions
        assert result["warnings"] == []

    async def test_returns_empty_chunks_with_warning(self) -> None:
        input_json = json.dumps({
            "name": "requests",
            "current_version": "2.28.0",
            "latest_version": "2.32.0",
        })

        with patch(
            "migratowl.agent.tools.changelog.fetch_changelog",
            new_callable=AsyncMock,
            return_value=("", ["Could not fetch changelog for requests"]),
        ):
            tool = create_fetch_changelog_tool()
            result = json.loads(await tool.ainvoke({"outdated_dep_json": input_json}))

        assert result["chunks"] == []
        assert len(result["warnings"]) == 1
        assert "requests" in result["warnings"][0]

    async def test_malformed_json_raises(self) -> None:
        tool = create_fetch_changelog_tool()
        with pytest.raises(json.JSONDecodeError):
            await tool.ainvoke({"outdated_dep_json": "not json"})
