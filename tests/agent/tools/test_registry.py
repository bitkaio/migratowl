# SPDX-License-Identifier: Apache-2.0

"""Tests for check_outdated_deps tool."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from migratowl.agent.tools.registry import create_check_outdated_tool
from migratowl.config import Settings
from migratowl.models.schemas import Dependency, Ecosystem, OutdatedCheckMode, OutdatedDependency
from migratowl.registry import CheckOptions, query_maven_central


def _make_outdated(name: str, current: str, latest: str) -> OutdatedDependency:
    return OutdatedDependency(
        name=name,
        current_version=current,
        latest_version=latest,
        ecosystem=Ecosystem.PYTHON,
        manifest_path="requirements.txt",
    )


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

        assert len(result["outdated"]) == 1
        assert result["outdated"][0]["name"] == "requests"
        assert result["outdated"][0]["latest_version"] == "2.32.0"
        assert result["warning"] is None

    async def test_empty_input(self) -> None:
        with patch("migratowl.agent.tools.registry.check_outdated", new_callable=AsyncMock, return_value=[]):
            tool = create_check_outdated_tool(concurrency=5)
            result = json.loads(await tool.ainvoke({"dependencies_json": "[]"}))

        assert result == {"outdated": [], "warning": None}

    async def test_malformed_json_raises(self) -> None:
        tool = create_check_outdated_tool(concurrency=5)
        with pytest.raises(json.JSONDecodeError):
            await tool.ainvoke({"dependencies_json": "not json"})

    async def test_under_cap_returns_no_warning(self) -> None:
        mock_outdated = [
            _make_outdated("dep_a", "1.0.0", "2.0.0"),
            _make_outdated("dep_b", "2.0.0", "3.0.0"),
            _make_outdated("dep_c", "1.0.0", "1.5.0"),
        ]
        deps_json = json.dumps([
            {"name": d.name, "current_version": d.current_version, "ecosystem": "python", "manifest_path": "requirements.txt"}
            for d in mock_outdated
        ])

        with patch("migratowl.agent.tools.registry.check_outdated", new_callable=AsyncMock, return_value=mock_outdated):
            tool = create_check_outdated_tool(concurrency=5)
            result = json.loads(await tool.ainvoke({"dependencies_json": deps_json}))

        assert result["warning"] is None
        assert len(result["outdated"]) == 3

    async def test_over_cap_caps_and_returns_warning(self) -> None:
        mock_outdated = [
            _make_outdated("dep_a", "1.0.0", "4.0.0"),  # gap=3
            _make_outdated("dep_b", "2.0.0", "3.0.0"),  # gap=1
            _make_outdated("dep_c", "1.0.0", "5.0.0"),  # gap=4 (highest)
            _make_outdated("dep_d", "1.0.0", "2.0.0"),  # gap=1
            _make_outdated("dep_e", "3.0.0", "3.5.0"),  # gap=0
        ]
        deps_json = json.dumps([
            {"name": d.name, "current_version": d.current_version, "ecosystem": "python", "manifest_path": "requirements.txt"}
            for d in mock_outdated
        ])

        mock_settings = MagicMock(spec=Settings)
        mock_settings.max_outdated_deps = 3

        with patch("migratowl.agent.tools.registry.get_settings", return_value=mock_settings):
            with patch("migratowl.agent.tools.registry.check_outdated", new_callable=AsyncMock, return_value=mock_outdated):
                tool = create_check_outdated_tool(concurrency=5)
                result = json.loads(await tool.ainvoke({"dependencies_json": deps_json}))

        assert result["warning"] is not None
        assert "Capped" in result["warning"]
        assert len(result["outdated"]) == 3

    async def test_highest_version_gap_first_when_capped(self) -> None:
        mock_outdated = [
            _make_outdated("dep_a", "1.0.0", "4.0.0"),  # gap=3
            _make_outdated("dep_b", "2.0.0", "3.0.0"),  # gap=1
            _make_outdated("dep_c", "1.0.0", "5.0.0"),  # gap=4 (highest)
            _make_outdated("dep_d", "1.0.0", "2.0.0"),  # gap=1
            _make_outdated("dep_e", "3.0.0", "3.5.0"),  # gap=0
        ]
        deps_json = json.dumps([
            {"name": d.name, "current_version": d.current_version, "ecosystem": "python", "manifest_path": "requirements.txt"}
            for d in mock_outdated
        ])

        mock_settings = MagicMock(spec=Settings)
        mock_settings.max_outdated_deps = 3

        with patch("migratowl.agent.tools.registry.get_settings", return_value=mock_settings):
            with patch("migratowl.agent.tools.registry.check_outdated", new_callable=AsyncMock, return_value=mock_outdated):
                tool = create_check_outdated_tool(concurrency=5)
                result = json.loads(await tool.ainvoke({"dependencies_json": deps_json}))

        returned_names = [d["name"] for d in result["outdated"]]
        # dep_c (gap=4) must be first, dep_a (gap=3) must be second
        assert returned_names[0] == "dep_c"
        assert returned_names[1] == "dep_a"
        # third must be dep_b or dep_d (both gap=1), not dep_e (gap=0)
        assert returned_names[2] in ("dep_b", "dep_d")

    async def test_check_outdated_called_with_options(self) -> None:
        """CheckOptions passed to the factory are forwarded to check_outdated."""
        deps_json = json.dumps([
            {"name": "requests", "current_version": "2.31.0", "ecosystem": "python", "manifest_path": "requirements.txt"},
        ])
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=True)

        with patch("migratowl.agent.tools.registry.check_outdated", new_callable=AsyncMock, return_value=[]) as mock_check:
            tool = create_check_outdated_tool(concurrency=5, options=opts)
            await tool.ainvoke({"dependencies_json": deps_json})

        mock_check.assert_called_once()
        _, kwargs = mock_check.call_args
        assert kwargs["options"] == opts


class TestQueryMavenCentral:
    async def test_returns_outdated_when_newer_available(self) -> None:
        dep = Dependency(
            name="org.springframework.boot:spring-boot-starter",
            current_version="3.2.0",
            ecosystem=Ecosystem.JAVA,
            manifest_path="pom.xml",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": {
                "docs": [{"v": "3.2.0"}, {"v": "3.3.0"}]
            }
        }
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.return_value = mock_response

        result = await query_maven_central(client, dep)

        assert result is not None
        assert result.latest_version == "3.3.0"
        assert result.current_version == "3.2.0"
        assert result.ecosystem == Ecosystem.JAVA
        assert client.get.call_args[0][0].startswith("https://search.maven.org/")
        assert "org.springframework.boot" in client.get.call_args[0][0]
        assert "spring-boot-starter" in client.get.call_args[0][0]
        assert "core=gav" in client.get.call_args[0][0]

    async def test_returns_none_when_up_to_date(self) -> None:
        dep = Dependency(
            name="com.example:library",
            current_version="2.0.0",
            ecosystem=Ecosystem.JAVA,
            manifest_path="pom.xml",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "response": {"docs": [{"v": "2.0.0"}]}
        }
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.return_value = mock_response

        result = await query_maven_central(client, dep)

        assert result is None

    async def test_returns_none_when_not_found(self) -> None:
        dep = Dependency(
            name="com.example:unknown",
            current_version="1.0.0",
            ecosystem=Ecosystem.JAVA,
            manifest_path="pom.xml",
        )
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"response": {"docs": []}}
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.return_value = mock_response

        result = await query_maven_central(client, dep)

        assert result is None

    async def test_returns_none_for_malformed_name(self) -> None:
        dep = Dependency(
            name="no-colon-here",
            current_version="1.0.0",
            ecosystem=Ecosystem.JAVA,
            manifest_path="pom.xml",
        )
        client = AsyncMock(spec=httpx.AsyncClient)

        result = await query_maven_central(client, dep)

        assert result is None
        client.get.assert_not_called()