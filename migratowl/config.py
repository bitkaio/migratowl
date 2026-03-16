"""Centralized configuration for MigratOwl."""

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


def get_settings() -> Settings:
    """Settings factory."""
    return Settings()
