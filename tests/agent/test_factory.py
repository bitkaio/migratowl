"""Tests for agent graph factory."""

import pytest
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
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", return_value=mock_graph),
        ):
            graph = create_migratowl_agent(mock_backend, settings=settings)

        assert graph is mock_graph

    def test_passes_backend_factory_to_deep_agent(self) -> None:
        mock_backend = MagicMock()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.create_deep_agent") as mock_create,
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
        ):
            create_migratowl_agent(mock_backend, settings=settings)

        call_kwargs = mock_create.call_args[1]
        assert callable(call_kwargs["backend"])

    def test_creates_expected_tool_count(self) -> None:
        mock_backend = MagicMock()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.create_deep_agent") as mock_create,
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
        ):
            create_migratowl_agent(mock_backend, settings=settings)

        call_kwargs = mock_create.call_args[1]
        # 8 tools: clone, copy, detect, scan, check_outdated, update, execute, changelog
        assert len(call_kwargs["tools"]) == 8  # noqa: PLR2004

    def test_uses_init_chat_model_with_provider_and_name(self) -> None:
        mock_backend = MagicMock()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.create_deep_agent"),
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
        ):
            create_migratowl_agent(mock_backend, settings=settings)

        mock_init.assert_called_once()
        model_id = mock_init.call_args[0][0]
        assert model_id == f"{settings.model_provider}:{settings.model_name}"

    def test_applies_session_injection(self) -> None:
        mock_backend = MagicMock()
        settings = Settings(_env_file=None)
        raw_graph = MagicMock()
        patched_graph = MagicMock()

        with (
            patch("migratowl.agent.factory.create_deep_agent", return_value=raw_graph),
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch(
                "migratowl.agent.factory.apply_session_injection", return_value=patched_graph
            ) as mock_inject,
        ):
            result = create_migratowl_agent(mock_backend, settings=settings)

        mock_inject.assert_called_once_with(raw_graph)
        assert result is patched_graph

    def test_passes_langfuse_handler_as_callback_when_configured(self) -> None:
        mock_backend = MagicMock()
        settings = Settings(_env_file=None)
        mock_handler = MagicMock()

        with (
            patch("migratowl.agent.factory.create_deep_agent"),
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory._langfuse_handler", mock_handler),
        ):
            create_migratowl_agent(mock_backend, settings=settings)

        call_kwargs = mock_init.call_args[1]
        assert call_kwargs["callbacks"] == [mock_handler]

    def test_no_langfuse_callback_when_not_configured(self) -> None:
        mock_backend = MagicMock()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.create_deep_agent"),
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory._langfuse_handler", None),
        ):
            create_migratowl_agent(mock_backend, settings=settings)

        call_kwargs = mock_init.call_args[1]
        assert call_kwargs.get("callbacks") is None

    def test_passes_base_url_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_backend = MagicMock()
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com")
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.create_deep_agent"),
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory._langfuse_handler", None),
        ):
            create_migratowl_agent(mock_backend, settings=settings)

        call_kwargs = mock_init.call_args[1]
        assert call_kwargs.get("base_url") == "https://proxy.example.com"

    def test_no_base_url_when_not_set(self) -> None:
        mock_backend = MagicMock()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.create_deep_agent"),
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory._langfuse_handler", None),
        ):
            create_migratowl_agent(mock_backend, settings=settings)

        call_kwargs = mock_init.call_args[1]
        assert "base_url" not in call_kwargs
