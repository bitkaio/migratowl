"""Centralized configuration for MigratOwl."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MIGRATOWL_", env_file=".env", extra="ignore")

    # LLM (API key is read by langchain-anthropic directly from ANTHROPIC_API_KEY)
    model_name: str = "claude-sonnet-4-6"

    # Kubernetes sandbox
    sandbox_template: str = "migratowl-sandbox-template"
    sandbox_namespace: str = "default"
    sandbox_connection_mode: str = "tunnel"

    # Workspace
    workspace_path: str = "/home/user/workspace"

    # Registry scanning
    scan_registry_concurrency: int = 10

    # Analysis
    confidence_threshold: float = 0.7
    max_output_chars: int = 30_000

    # GitHub
    github_token: str = Field(default="", validation_alias=AliasChoices("GITHUB_TOKEN"))

    # Rate limiting
    model_rate_limit_rps: float = 0.1  # requests per second; 0.1 = 6 req/min

    # Changelog
    max_changelog_chars: int = 15_000

    # Registry output cap
    max_outdated_deps: int = 100

    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # HTTP client
    http_timeout: float = 30.0
    http_retry_count: int = 3
    http_retry_backoff_base: float = 0.5


def get_settings() -> Settings:
    """Settings factory."""
    return Settings()
