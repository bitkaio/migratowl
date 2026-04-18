# SPDX-License-Identifier: Apache-2.0

"""Tests for sandbox manager factory."""

from unittest.mock import patch

import pytest

from migratowl.agent.sandbox import create_sandbox_manager
from migratowl.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def raw_settings() -> Settings:
    return Settings(_env_file=None, sandbox_mode="raw")


class TestCreateSandboxManagerAgentSandboxMode:
    def test_returns_manager_instance(self, settings: Settings) -> None:
        with patch("migratowl.agent.sandbox.KubernetesSandboxManager") as mock_cls:
            mock_cls.return_value = mock_cls
            result = create_sandbox_manager(settings)

        mock_cls.assert_called_once()
        assert result is mock_cls

    def test_passes_agent_sandbox_config(self, settings: Settings) -> None:
        with patch(
            "migratowl.agent.sandbox.KubernetesProviderConfig"
        ) as mock_config_cls, patch("migratowl.agent.sandbox.KubernetesSandboxManager"):
            create_sandbox_manager(settings)

        mock_config_cls.assert_called_once_with(
            template_name=settings.sandbox_template,
            namespace=settings.sandbox_namespace,
            connection_mode=settings.sandbox_connection_mode,
        )


class TestCreateSandboxManagerRawMode:
    def test_returns_manager_instance(self, raw_settings: Settings) -> None:
        with patch("migratowl.agent.sandbox.KubernetesSandboxManager") as mock_cls:
            mock_cls.return_value = mock_cls
            result = create_sandbox_manager(raw_settings)

        mock_cls.assert_called_once()
        assert result is mock_cls

    def test_passes_raw_config(self, raw_settings: Settings) -> None:
        with patch(
            "migratowl.agent.sandbox.KubernetesProviderConfig"
        ) as mock_config_cls, patch("migratowl.agent.sandbox.KubernetesSandboxManager"):
            create_sandbox_manager(raw_settings)

        mock_config_cls.assert_called_once_with(
            mode="raw",
            namespace=raw_settings.sandbox_namespace,
            image=raw_settings.sandbox_image,
            block_network=raw_settings.sandbox_block_network,
        )