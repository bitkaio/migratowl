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

"""The version a report records: the package version, plus the commit when run from a git checkout."""

import functools
import subprocess
from pathlib import Path

from migratowl import __version__

_SOURCE_DIR = Path(__file__).resolve().parent


@functools.cache
def build_version(source_dir: Path = _SOURCE_DIR) -> str:
    """``0.9.0``, or ``0.9.0+g<commit>`` when ``source_dir`` is inside a git checkout (images have no .git)."""
    try:
        commit = subprocess.run(
            ["git", "-C", str(source_dir), "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return __version__
    return f"{__version__}+g{commit}" if commit else __version__
