"""Tests for MigratOwl configuration."""

from migratowl.config import Settings, get_settings


class TestSettingsDefaults:
    def test_default_model_name(self) -> None:
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

    def test_default_workspace_path(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.workspace_path == "/home/user/workspace"

    def test_default_scan_registry_concurrency(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.scan_registry_concurrency == 10

    def test_default_github_token(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.github_token == ""

    def test_default_http_timeout(self) -> None:
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

    def test_default_max_output_chars(self) -> None:
        settings = Settings(_env_file=None)
        assert settings.max_output_chars == 30_000


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


class TestGetSettings:
    def test_returns_settings_instance(self) -> None:
        result = get_settings()
        assert isinstance(result, Settings)
