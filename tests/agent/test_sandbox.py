# SPDX-License-Identifier: Apache-2.0

"""Tests for sandbox manager factory."""

import os
from unittest.mock import patch

import pytest

from migratowl.agent.sandbox import create_sandbox_manager
from migratowl.config import Settings


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("MIGRATOWL_SANDBOX_MODE", raising=False)
    monkeypatch.delenv("MIGRATOWL_SANDBOX_IMAGE", raising=False)
    monkeypatch.delenv("MIGRATOWL_SANDBOX_BLOCK_NETWORK", raising=False)
    return Settings(_env_file=None)


@pytest.fixture
def raw_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("MIGRATOWL_SANDBOX_MODE", raising=False)
    monkeypatch.delenv("MIGRATOWL_SANDBOX_IMAGE", raising=False)
    monkeypatch.delenv("MIGRATOWL_SANDBOX_BLOCK_NETWORK", raising=False)
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
            kube_api_url=settings.sandbox_kube_api_url,
            kube_token=settings.sandbox_kube_token,
            api_url=settings.sandbox_api_url,
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


class TestCreateSandboxManagerTTL:
    def test_manager_created_with_ttls(self, settings: Settings) -> None:
        """TTLs from settings are passed to the manager so leaked pods get reaped."""
        with (
            patch("migratowl.agent.sandbox.KubernetesProviderConfig"),
            patch("migratowl.agent.sandbox.KubernetesSandboxManager") as mock_mgr,
        ):
            create_sandbox_manager(settings)

        call_kwargs = mock_mgr.call_args[1]
        assert call_kwargs["ttl_seconds"] == settings.sandbox_ttl_seconds
        assert call_kwargs["ttl_idle_seconds"] == settings.sandbox_ttl_idle_seconds

class TestKubeApiWiring:
    """agent-sandbox mode lists/deletes SandboxClaims over raw HTTP; off-cluster
    it needs an explicit API URL (e.g. `kubectl proxy`), else cleanup can't run."""

    def test_passes_kube_api_url_and_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_SANDBOX_KUBE_API_URL", "http://localhost:8001")
        monkeypatch.setenv("MIGRATOWL_SANDBOX_KUBE_TOKEN", "tok")
        settings = Settings(_env_file=None)
        with patch("migratowl.agent.sandbox.KubernetesProviderConfig") as mock_config_cls, \
             patch("migratowl.agent.sandbox.KubernetesSandboxManager"):
            create_sandbox_manager(settings)

        kwargs = mock_config_cls.call_args.kwargs
        assert kwargs["kube_api_url"] == "http://localhost:8001"
        assert kwargs["kube_token"] == "tok"


class TestDirectConnection:
    """Inside the cluster there is no kubectl to tunnel with: the sandbox-router is reached by URL."""

    def test_router_url_reaches_the_provider_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_SANDBOX_CONNECTION_MODE", "direct")
        monkeypatch.setenv("MIGRATOWL_SANDBOX_API_URL", "http://sandbox-router-svc.default.svc.cluster.local:8080")
        settings = Settings(_env_file=None)
        with patch("migratowl.agent.sandbox.KubernetesProviderConfig") as mock_config_cls, \
             patch("migratowl.agent.sandbox.KubernetesSandboxManager"):
            create_sandbox_manager(settings)

        kwargs = mock_config_cls.call_args.kwargs
        assert kwargs["connection_mode"] == "direct"
        assert kwargs["api_url"] == "http://sandbox-router-svc.default.svc.cluster.local:8080"

    def test_direct_mode_without_a_url_is_rejected_at_startup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_SANDBOX_CONNECTION_MODE", "direct")
        with pytest.raises(ValueError, match="MIGRATOWL_SANDBOX_API_URL"):
            Settings(_env_file=None)
