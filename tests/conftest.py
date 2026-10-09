# SPDX-License-Identifier: Apache-2.0

"""Shared test fixtures and helpers."""

import os
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

# Importing migratowl.api.main runs load_dotenv(), which copies a developer's real .env into os.environ for
# the whole session. Tests must start from a clean environment: no real keys, tokens or settings.
_AMBIENT_PREFIXES = ("MIGRATOWL_", "LANGFUSE_", "UV_INDEX_")
_AMBIENT_NAMES = {
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN", "GITLAB_TOKEN", "GITHUB_API_URL", "GITLAB_API_URL",
    "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "LITELLM_BASE_URL",
}


@pytest.fixture(autouse=True)
def _clean_ambient_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name in _AMBIENT_NAMES or name.startswith(_AMBIENT_PREFIXES):
            monkeypatch.delenv(name, raising=False)


@dataclass
class ExecResult:
    """Fake sandbox execution result for unit tests."""

    output: str
    exit_code: int


def make_backend(output: str = "", exit_code: int = 0) -> MagicMock:
    """Create a mock sandbox backend with a preset execute result."""
    backend = MagicMock()
    backend.execute.return_value = ExecResult(output=output, exit_code=exit_code)
    return backend