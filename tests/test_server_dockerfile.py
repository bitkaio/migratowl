# SPDX-License-Identifier: Apache-2.0

"""Tests for the server image (Dockerfile at the repository root)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"


def _instructions() -> list[str]:
    joined = re.sub(r"\\\n", " ", DOCKERFILE.read_text())
    return [line.strip() for line in joined.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_every_base_image_is_pinned_by_digest() -> None:
    refs = [i.split()[1] for i in _instructions() if i.startswith("FROM ")]
    refs += [m for i in _instructions() for m in re.findall(r"--from=(\S+)", i) if "/" in m]
    assert refs
    for ref in refs:
        assert re.search(r"@sha256:[0-9a-f]{64}$", ref), f"{ref} is not pinned by digest"


def test_runs_as_a_non_root_user() -> None:
    users = [i.split(None, 1)[1] for i in _instructions() if i.startswith("USER ")]
    assert users and users[-1].split(":")[0] not in ("root", "0")


def test_starts_uvicorn_as_a_single_process() -> None:
    cmd = next(i for i in _instructions() if i.startswith("CMD "))
    assert "migratowl.api.main:app" in cmd and "uvicorn" in cmd
    assert "--workers" not in cmd  # SQLite jobs and startup reconciliation assume one process


def test_state_lives_on_the_data_volume() -> None:
    env = " ".join(i for i in _instructions() if i.startswith("ENV "))
    assert "MIGRATOWL_JOBS_DB_PATH=/data/" in env
    assert "MIGRATOWL_CHECKPOINT_DB_PATH=/data/" in env
    assert any(i.startswith("VOLUME /data") for i in _instructions())


def test_build_context_leaves_out_secrets_and_ai_files() -> None:
    ignored = (ROOT / ".dockerignore").read_text().split()
    for entry in (".env", ".git", ".claude", "CLAUDE.md"):
        assert entry in ignored
