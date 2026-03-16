"""Tests for check_outdated_deps tool."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from migratowl.agent.tools.registry import create_check_outdated_tool
from migratowl.models.schemas import Ecosystem, OutdatedDependency


class TestCheckOutdatedDepsTool:
    async def test_valid_json_input(self) -> None:
        deps_json = json.dumps([
            {"name": "requests", "current_version": "2.31.0", "ecosystem": "python", "manifest_path": "requirements.txt"},
        ])
        mock_outdated = [
            OutdatedDependency(
                name="requests",
                current_version="2.31.0",
                latest_version="2.32.0",
                ecosystem=Ecosystem.PYTHON,
                manifest_path="requirements.txt",
                repository_url="https://github.com/psf/requests",
            ),
        ]

        with patch("migratowl.agent.tools.registry.check_outdated", new_callable=AsyncMock, return_value=mock_outdated):
            tool = create_check_outdated_tool(concurrency=5)
            result = json.loads(await tool.ainvoke({"dependencies_json": deps_json}))

        assert len(result) == 1
        assert result[0]["name"] == "requests"
        assert result[0]["latest_version"] == "2.32.0"

    async def test_empty_input(self) -> None:
        with patch("migratowl.agent.tools.registry.check_outdated", new_callable=AsyncMock, return_value=[]):
            tool = create_check_outdated_tool(concurrency=5)
            result = json.loads(await tool.ainvoke({"dependencies_json": "[]"}))

        assert result == []

    async def test_malformed_json_raises(self) -> None:
        tool = create_check_outdated_tool(concurrency=5)
        with pytest.raises(json.JSONDecodeError):
            await tool.ainvoke({"dependencies_json": "not json"})
