# SPDX-License-Identifier: Apache-2.0

"""Guards on dependency ranges that would otherwise break at install time."""

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _requirement(name: str) -> Requirement:
    deps = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    return next(Requirement(d) for d in deps if Requirement(d).name == name)


def test_deepagents_capped_below_0_7() -> None:
    # deepagents 0.7 removes the callable-backend API the agent factory and the
    # package-analyzer subagent rely on; an unbounded range lets pip pick it.
    spec = _requirement("deepagents").specifier
    assert Version("0.6.12") in spec
    assert Version("0.7.0") not in spec
