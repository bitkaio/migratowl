"""Tests for sandbox lifecycle helpers."""

from unittest.mock import MagicMock, patch

import pytest

from migratowl.agent.sandbox import create_sandbox, destroy_sandbox
from migratowl.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


class TestCreateSandbox:
    @pytest.mark.asyncio
    async def test_creates_provider_and_sandbox(self, settings: Settings) -> None:
        mock_sandbox = MagicMock()
        mock_sandbox.id = "sandbox-123"
        mock_provider = MagicMock()
        mock_provider.get_or_create.return_value = mock_sandbox

        with patch(
            "migratowl.agent.sandbox.KubernetesProvider", return_value=mock_provider
        ) as mock_cls:
            provider, sandbox = await create_sandbox(settings)

        mock_cls.assert_called_once()
        mock_provider.get_or_create.assert_called_once()
        assert sandbox.id == "sandbox-123"
        assert provider is mock_provider

    @pytest.mark.asyncio
    async def test_passes_settings_to_config(self, settings: Settings) -> None:
        mock_sandbox = MagicMock()
        mock_provider = MagicMock()
        mock_provider.get_or_create.return_value = mock_sandbox

        with patch(
            "migratowl.agent.sandbox.KubernetesProvider", return_value=mock_provider
        ), patch("migratowl.agent.sandbox.KubernetesProviderConfig") as mock_config_cls:
            await create_sandbox(settings)

        mock_config_cls.assert_called_once_with(
            template_name=settings.sandbox_template,
            namespace=settings.sandbox_namespace,
            connection_mode=settings.sandbox_connection_mode,
        )


class TestDestroySandbox:
    @pytest.mark.asyncio
    async def test_calls_delete(self) -> None:
        mock_sandbox = MagicMock()
        mock_sandbox.id = "sandbox-123"
        mock_provider = MagicMock()

        await destroy_sandbox(mock_provider, mock_sandbox)

        mock_provider.delete.assert_called_once_with(sandbox_id="sandbox-123")

    @pytest.mark.asyncio
    async def test_suppresses_exceptions(self) -> None:
        mock_sandbox = MagicMock()
        mock_sandbox.id = "sandbox-123"
        mock_provider = MagicMock()
        mock_provider.delete.side_effect = RuntimeError("cluster gone")

        # Should not raise
        await destroy_sandbox(mock_provider, mock_sandbox)
