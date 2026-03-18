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

    async def test_large_changelog_is_reduced(self) -> None:
        """Large changelog output should be under max_changelog_chars."""
        # Build a changelog with many large breaking-change entries (so extraction keeps them)
        versions = [f"## {i}.0.0\nBREAKING CHANGE: {'x' * 5000}\n" for i in range(50, 0, -1)]
        big_changelog = "\n".join(versions)

        input_json = json.dumps({
            "name": "bigpkg",
            "current_version": "0.0.0",
            "latest_version": "50.0.0",
            "changelog_url": "https://example.com/CHANGELOG.md",
        })

        with (
            patch(
                "migratowl.agent.tools.changelog.fetch_changelog",
                new_callable=AsyncMock,
                return_value=(big_changelog, []),
            ),
            patch("migratowl.agent.tools.changelog.get_settings") as mock_settings,
        ):
            mock_settings.return_value.max_changelog_chars = 5_000
            tool = create_fetch_changelog_tool()
            raw = await tool.ainvoke({"outdated_dep_json": input_json})

        assert len(raw) <= 5_000 + 500  # some overhead for JSON envelope + warnings

    async def test_truncation_warning_added(self) -> None:
        """When truncation occurs, warnings should include the truncation message."""
        versions = [f"## {i}.0.0\nBREAKING CHANGE: {'y' * 3000}\n" for i in range(20, 0, -1)]
        big_changelog = "\n".join(versions)

        input_json = json.dumps({
            "name": "bigpkg",
            "current_version": "0.0.0",
            "latest_version": "20.0.0",
            "changelog_url": "https://example.com/CHANGELOG.md",
        })

        with (
            patch(
                "migratowl.agent.tools.changelog.fetch_changelog",
                new_callable=AsyncMock,
                return_value=(big_changelog, []),
            ),
            patch("migratowl.agent.tools.changelog.get_settings") as mock_settings,
        ):
            mock_settings.return_value.max_changelog_chars = 2_000
            tool = create_fetch_changelog_tool()
            result = json.loads(await tool.ainvoke({"outdated_dep_json": input_json}))

        assert any("truncated" in w.lower() for w in result["warnings"])
