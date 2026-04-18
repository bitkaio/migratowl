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
