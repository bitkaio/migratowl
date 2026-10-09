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


def test_langchain_kubernetes_comes_from_the_maintained_fork() -> None:
    # The fork carries fixes Migratowl depends on (raw-mode NetworkPolicy cleanup,
    # agent-sandbox reconnect in tunnel mode); PyPI's 0.4.0 has neither.
    req = _requirement("langchain-kubernetes")
    assert req.url == "git+https://github.com/barnakun/langchain-kubernetes@py-0.4.1#subdirectory=python"
    assert "agent-sandbox" in req.extras


def test_uvicorn_is_a_runtime_dependency() -> None:
    # The server is started with `uvicorn migratowl.api.main:app`; it used to arrive only through the
    # dev group (langgraph-cli), so a production install (`uv sync --no-dev`) could not start.
    assert _requirement("uvicorn").name == "uvicorn"
