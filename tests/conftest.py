# SPDX-License-Identifier: Apache-2.0

"""Shared test fixtures and helpers."""

from dataclasses import dataclass
from unittest.mock import MagicMock


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