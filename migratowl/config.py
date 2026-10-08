# Copyright bitkaio LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Centralized configuration for Migratowl."""

from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MIGRATOWL_", extra="ignore")

    # LLM — API keys read directly from env: ANTHROPIC_API_KEY or OPENAI_API_KEY
    model_provider: Literal["anthropic", "openai", "litellm"] = "anthropic"
    model_name: str = "claude-sonnet-5-5"
    anthropic_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MIGRATOWL_ANTHROPIC_BASE_URL", "ANTHROPIC_BASE_URL"),
    )
    openai_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MIGRATOWL_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
    )
    # LiteLLM unified endpoint (OpenAI-compatible, routes to any provider)
    litellm_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MIGRATOWL_LITELLM_BASE_URL", "LITELLM_BASE_URL"),
    )
    # Model alias — override model name sent to provider (for proxies with different naming)
    model_alias: str | None = Field(
        default=None,
        description="Override model name sent to provider. Use when proxy expects different naming.",
    )

    # Kubernetes sandbox
    sandbox_mode: Literal["agent-sandbox", "raw"] = "agent-sandbox"
    sandbox_template: str = "migratowl-sandbox-template"
    sandbox_namespace: str = "default"
    sandbox_connection_mode: str = "tunnel"
    # Raw mode only. Needs git plus each ecosystem toolchain (see k8s/runtime/).
    sandbox_image: str = "ghcr.io/bitkaio/migratowl-runtime:latest"
    sandbox_block_network: bool = True
    # Python version in the sandbox image (k8s/runtime/Dockerfile). PyPI releases
    # whose requires_python excludes it are not suggested as upgrades.
    sandbox_python_version: str = "3.13"
    # Kubernetes API for SandboxClaim list/delete (agent-sandbox mode). Unset means
    # the in-cluster URL; off-cluster point it at `kubectl proxy` (http://localhost:8001)
    # or pass a bearer token, otherwise TTL sweep and shutdown cleanup can't run.
    sandbox_kube_api_url: str | None = None
    sandbox_kube_token: str | None = None

    # Workspace
    workspace_path: str = "/home/user/workspace"

    # Concurrency — resource throttle limiting simultaneous scans (each job runs
    # in its own sandbox pod, so this bounds sandbox/LLM load, NOT path collisions).
    max_concurrent_scans: int = 1

    # Sandbox TTL / leak reconciliation. Absolute TTL defaults to None (no
    # guillotine — long compiles must not be reaped mid-scan); rely on the
    # activity-aware idle TTL instead.
    sandbox_ttl_seconds: int | None = None
    sandbox_ttl_idle_seconds: int | None = 1800

    # Persistence / crash recovery. "sqlite" (default) persists jobs + agent
    # checkpoints so scans survive a restart; "memory" is non-durable (CI/ephemeral).
    persistence_backend: Literal["memory", "sqlite"] = "sqlite"
    jobs_db_path: str = "./migratowl_jobs.db"
    checkpoint_db_path: str = "./migratowl_checkpoints.db"
    max_scan_retries: int = 3

    # Registry scanning
    scan_registry_concurrency: int = 10

    # Analysis
    confidence_threshold: float = 0.7
    max_output_chars: int = 30_000
    # Characters of failing validation output (tail) given to the LLM in the analysis brief.
    analysis_tail_chars: int = 4_000

    # Git providers
    github_token: str = Field(
        default="",
        validation_alias=AliasChoices("MIGRATOWL_GITHUB_TOKEN", "GITHUB_TOKEN"),
    )
    gitlab_token: str = Field(
        default="",
        validation_alias=AliasChoices("MIGRATOWL_GITLAB_TOKEN", "GITLAB_TOKEN"),
    )
    github_api_url: str = Field(
        default="https://api.github.com",
        validation_alias=AliasChoices("MIGRATOWL_GITHUB_API_URL", "GITHUB_API_URL"),
    )
    gitlab_api_url: str = Field(
        default="https://gitlab.com/api/v4",
        validation_alias=AliasChoices("MIGRATOWL_GITLAB_API_URL", "GITLAB_API_URL"),
    )

    # LangFuse — optional observability (keys read from standard LangFuse env vars)
    langfuse_public_key: str = Field(
        default="",
        validation_alias=AliasChoices("MIGRATOWL_LANGFUSE_PUBLIC_KEY", "LANGFUSE_PUBLIC_KEY"),
    )
    langfuse_secret_key: str = Field(
        default="",
        validation_alias=AliasChoices("MIGRATOWL_LANGFUSE_SECRET_KEY", "LANGFUSE_SECRET_KEY"),
    )
    langfuse_host: str = Field(
        default="https://cloud.langfuse.com",
        validation_alias=AliasChoices("MIGRATOWL_LANGFUSE_HOST", "LANGFUSE_HOST"),
    )

    # Rate limiting
    model_rate_limit_rps: float = 0.1  # requests per second; 0.1 = 6 req/min

    # Changelog
    max_changelog_chars: int = 15_000

    # Registry output cap
    max_outdated_deps: int = 100

    # API security. When api_token is set, /webhook and /jobs* require
    # "Authorization: Bearer <token>". callback_url may not target private,
    # loopback or link-local addresses unless callback_allow_private is set.
    api_token: str | None = None
    callback_allow_private: bool = False

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