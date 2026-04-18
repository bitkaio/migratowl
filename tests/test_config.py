# SPDX-License-Identifier: Apache-2.0

"""Tests for Migratowl configuration."""

import pytest
from pydantic import ValidationError

from migratowl.config import Settings, get_settings


class TestSettingsDefaults:
    def test_default_model_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MIGRATOWL_MODEL_NAME", raising=False)
        settings = Settings(_env_file=None)
        assert settings.model_name == "claude-sonnet-4-6"

    def test_default_sandbox_template(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.sandbox_template == "migratowl-sandbox-template"

    def test_default_sandbox_namespace(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.sandbox_namespace == "default"

    def test_default_sandbox_connection_mode(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.sandbox_connection_mode == "tunnel"

    def test_default_sandbox_mode(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.sandbox_mode == "agent-sandbox"

    def test_default_sandbox_image(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.sandbox_image == "python:3.12-slim"

    def test_default_sandbox_block_network(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.sandbox_block_network is True

    def test_default_workspace_path(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.workspace_path == "/home/user/workspace"

    def test_default_scan_registry_concurrency(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MIGRATOWL_SCAN_REGISTRY_CONCURRENCY", raising=False)
        settings = Settings(_env_file=None)
        assert settings.scan_registry_concurrency == 10

    def test_default_github_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("MIGRATOWL_GITHUB_TOKEN", raising=False)
        settings = Settings(_env_file=None)
        assert settings.github_token == ""

    def test_default_gitlab_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        monkeypatch.delenv("MIGRATOWL_GITLAB_TOKEN", raising=False)
        settings = Settings(_env_file=None)
        assert settings.gitlab_token == ""

    def test_default_github_api_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_API_URL", raising=False)
        monkeypatch.delenv("MIGRATOWL_GITHUB_API_URL", raising=False)
        settings = Settings(_env_file=None)
        assert settings.github_api_url == "https://api.github.com"

    def test_default_gitlab_api_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITLAB_API_URL", raising=False)
        monkeypatch.delenv("MIGRATOWL_GITLAB_API_URL", raising=False)
        settings = Settings(_env_file=None)
        assert settings.gitlab_api_url == "https://gitlab.com/api/v4"

    def test_default_http_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MIGRATOWL_HTTP_TIMEOUT", raising=False)
        settings = Settings(_env_file=None)
        assert settings.http_timeout == 30.0

    def test_default_http_retry_count(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.http_retry_count == 3

    def test_default_http_retry_backoff_base(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.http_retry_backoff_base == 0.5

    def test_default_confidence_threshold(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.confidence_threshold == 0.7

    def test_default_max_output_chars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MIGRATOWL_MAX_OUTPUT_CHARS", raising=False)
        settings = Settings(_env_file=None)
        assert settings.max_output_chars == 30_000

    def test_default_model_provider(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.model_provider == "anthropic"

    def test_default_api_host(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.api_host == "0.0.0.0"

    def test_default_api_port(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.api_port == 8000


class TestSettingsFromEnv:
    def test_env_override_model_name(self, monkeypatch: object) -> None:
        monkeypatch.setenv("MIGRATOWL_MODEL_NAME", "claude-opus-4-6")
        settings = Settings()
        assert settings.model_name == "claude-opus-4-6"

    def test_env_override_sandbox_template(self, monkeypatch: object) -> None:
        monkeypatch.setenv("MIGRATOWL_SANDBOX_TEMPLATE", "custom-template")
        settings = Settings()
        assert settings.sandbox_template == "custom-template"

    def test_env_override_sandbox_namespace(self, monkeypatch: object) -> None:
        monkeypatch.setenv("MIGRATOWL_SANDBOX_NAMESPACE", "staging")
        settings = Settings()
        assert settings.sandbox_namespace == "staging"

    def test_env_override_sandbox_connection_mode(self, monkeypatch: object) -> None:
        monkeypatch.setenv("MIGRATOWL_SANDBOX_CONNECTION_MODE", "direct")
        settings = Settings()
        assert settings.sandbox_connection_mode == "direct"

    def test_env_override_sandbox_mode_raw(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_SANDBOX_MODE", "raw")
        settings = Settings(_env_file=None)
        assert settings.sandbox_mode == "raw"

    def test_env_override_sandbox_image(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_SANDBOX_IMAGE", "node:20-slim")
        settings = Settings(_env_file=None)
        assert settings.sandbox_image == "node:20-slim"

    def test_invalid_sandbox_mode_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_SANDBOX_MODE", "docker")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_env_override_workspace_path(self, monkeypatch: object) -> None:
        monkeypatch.setenv("MIGRATOWL_WORKSPACE_PATH", "/tmp/workspace")
        settings = Settings()
        assert settings.workspace_path == "/tmp/workspace"

    def test_env_override_scan_registry_concurrency(self, monkeypatch: object) -> None:
        monkeypatch.setenv("MIGRATOWL_SCAN_REGISTRY_CONCURRENCY", "20")
        settings = Settings()
        assert settings.scan_registry_concurrency == 20

    def test_github_token_from_env(self, monkeypatch: object) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        settings = Settings()
        assert settings.github_token == "ghp_test123"

    def test_http_timeout_from_env(self, monkeypatch: object) -> None:
        monkeypatch.setenv("MIGRATOWL_HTTP_TIMEOUT", "60.0")
        settings = Settings()
        assert settings.http_timeout == 60.0

    def test_gitlab_token_from_standard_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-abc123")
        settings = Settings(_env_file=None)
        assert settings.gitlab_token == "glpat-abc123"

    def test_gitlab_token_from_migratowl_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_GITLAB_TOKEN", "glpat-xyz")
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
        settings = Settings(_env_file=None)
        assert settings.gitlab_token == "glpat-xyz"

    def test_github_api_url_for_ghes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_API_URL", "https://github.corp.com/api/v3")
        settings = Settings(_env_file=None)
        assert settings.github_api_url == "https://github.corp.com/api/v3"

    def test_gitlab_api_url_for_self_hosted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_GITLAB_API_URL", "https://gitlab.internal.com/api/v4")
        settings = Settings(_env_file=None)
        assert settings.gitlab_api_url == "https://gitlab.internal.com/api/v4"

    def test_env_override_model_provider_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_MODEL_PROVIDER", "openai")
        settings = Settings()
        assert settings.model_provider == "openai"

    def test_invalid_model_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_MODEL_PROVIDER", "gemini")
        with pytest.raises(ValidationError):
            Settings()


class TestBaseUrlSettings:
    def test_default_anthropic_base_url_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        settings = Settings(_env_file=None)
        assert settings.anthropic_base_url is None

    def test_default_openai_base_url_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        settings = Settings(_env_file=None)
        assert settings.openai_base_url is None

    def test_anthropic_base_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com/anthropic")
        settings = Settings()
        assert settings.anthropic_base_url == "https://proxy.example.com/anthropic"

    def test_openai_base_url_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example.com/openai/v1")
        settings = Settings()
        assert settings.openai_base_url == "https://proxy.example.com/openai/v1"


class TestMigraTOwlPrefixedAliases:
    """Fields with AliasChoices must also be settable via MIGRATOWL_ prefix."""

    def test_migratowl_github_token_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_GITHUB_TOKEN", "ghp_prefixed")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        settings = Settings()
        assert settings.github_token == "ghp_prefixed"

    def test_migratowl_anthropic_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_ANTHROPIC_BASE_URL", "https://proxy.internal/anthropic")
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        settings = Settings()
        assert settings.anthropic_base_url == "https://proxy.internal/anthropic"

    def test_migratowl_openai_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_OPENAI_BASE_URL", "https://proxy.internal/openai")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        settings = Settings()
        assert settings.openai_base_url == "https://proxy.internal/openai"

    def test_migratowl_langfuse_public_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_LANGFUSE_PUBLIC_KEY", "pk-migratowl")
        monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
        settings = Settings()
        assert settings.langfuse_public_key == "pk-migratowl"

    def test_migratowl_langfuse_secret_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_LANGFUSE_SECRET_KEY", "sk-migratowl")
        monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
        settings = Settings()
        assert settings.langfuse_secret_key == "sk-migratowl"

    def test_migratowl_langfuse_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_LANGFUSE_HOST", "https://langfuse.internal")
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        settings = Settings()
        assert settings.langfuse_host == "https://langfuse.internal"

    def test_migratowl_github_api_url_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATOWL_GITHUB_API_URL", "https://github.corp.com/api/v3")
        monkeypatch.delenv("GITHUB_API_URL", raising=False)
        settings = Settings(_env_file=None)
        assert settings.github_api_url == "https://github.corp.com/api/v3"


class TestGetSettings:
    def test_returns_settings_instance(self) -> None:
        result = get_settings()
        assert isinstance(result, Settings)