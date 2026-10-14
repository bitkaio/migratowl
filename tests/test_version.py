# SPDX-License-Identifier: Apache-2.0

"""Tests for the build version recorded on every report."""

import subprocess
from pathlib import Path

from migratowl import __version__


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


class TestBuildVersion:
    def test_package_version_outside_a_git_checkout(self, tmp_path: Path) -> None:
        from migratowl.version import build_version

        assert build_version(tmp_path) == __version__

    def test_appends_the_commit_inside_a_git_checkout(self, tmp_path: Path) -> None:
        from migratowl.version import build_version

        _git(tmp_path, "init", "-q")
        _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "init")
        sha = _git(tmp_path, "rev-parse", "--short=12", "HEAD")

        assert build_version(tmp_path) == f"{__version__}+g{sha}"
