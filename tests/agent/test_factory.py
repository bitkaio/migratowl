"""Tests for agent graph factory."""

from unittest.mock import MagicMock, patch

from migratowl.agent.factory import create_migratowl_agent
from migratowl.config import Settings


class TestCreateMigratowlAgent:
    def test_returns_compiled_graph(self) -> None:
        mock_backend = MagicMock()
        settings = Settings(_env_file=None)
        mock_graph = MagicMock()

        with (
            patch("migratowl.agent.factory.create_deep_agent", return_value=mock_graph),
            patch("migratowl.agent.factory.ChatAnthropic"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
        ):
            graph = create_migratowl_agent(mock_backend, settings=settings)

        assert graph is mock_graph

    def test_passes_backend_factory_to_deep_agent(self) -> None:
        mock_backend = MagicMock()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.create_deep_agent") as mock_create,
            patch("migratowl.agent.factory.ChatAnthropic"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
        ):
            create_migratowl_agent(mock_backend, settings=settings)

        call_kwargs = mock_create.call_args[1]
        assert callable(call_kwargs["backend"])

    def test_creates_expected_tool_count(self) -> None:
        mock_backend = MagicMock()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.create_deep_agent") as mock_create,
            patch("migratowl.agent.factory.ChatAnthropic"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
        ):
            create_migratowl_agent(mock_backend, settings=settings)

        call_kwargs = mock_create.call_args[1]
        # 8 tools: clone, copy, detect, scan, check_outdated, update, execute, changelog
        assert len(call_kwargs["tools"]) == 8  # noqa: PLR2004

    def test_uses_settings_model_name(self) -> None:
        mock_backend = MagicMock()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.create_deep_agent"),
            patch("migratowl.agent.factory.ChatAnthropic") as mock_chat,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
        ):
            create_migratowl_agent(mock_backend, settings=settings)

        mock_chat.assert_called_once()
        assert mock_chat.call_args[1]["model"] == settings.model_name
