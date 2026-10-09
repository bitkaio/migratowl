# SPDX-License-Identifier: Apache-2.0

"""Tests for the sandbox runtime image (k8s/runtime/Dockerfile)."""

import re
from pathlib import Path

RUNTIME_DOCKERFILE = Path(__file__).resolve().parent.parent / "k8s" / "runtime" / "Dockerfile"


def _instructions() -> list[str]:
    """Dockerfile instructions with line continuations joined, comments dropped."""
    joined = re.sub(r"\\\n", " ", RUNTIME_DOCKERFILE.read_text())
    return [line.strip() for line in joined.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def test_path_includes_java_build_tools() -> None:
    # validate_project runs `mvn` / `gradle`; without them every Java scan fails.
    env = " ".join(i for i in _instructions() if i.startswith("ENV "))
    assert "JAVA_HOME=" in env
    for tool_dir in ("/opt/java/bin", "/opt/maven/bin", "/opt/gradle/bin"):
        assert tool_dir in env, f"{tool_dir} missing from PATH"


def test_every_download_is_checksum_verified() -> None:
    runs = [i for i in _instructions() if i.startswith("RUN ")]
    downloads = [(run, target) for run in runs for target in re.findall(r"curl [^&]*?-o (\S+)", run)]
    assert len(downloads) >= 5, "expected Go, JDK, Maven and Gradle downloads"
    for run, target in downloads:
        assert re.search(rf"sha(256|512)sum -c[^&]*", run), f"{target}: no checksum check"
        assert re.search(rf'"(\$\{{\w+\}}|[0-9a-f]{{64,128}})  {re.escape(target)}"', run), (
            f"{target}: checksum not tied to file"
        )


def test_no_download_is_piped_straight_into_tar() -> None:
    text = " ".join(_instructions())
    assert not re.search(r"curl [^&|]*\|\s*tar", text), "archives must be verified before extraction"


def test_rustup_installer_is_pinned_to_a_release() -> None:
    # https://sh.rustup.rs always serves the newest script, so a pinned checksum
    # for it breaks the build on every rustup release.
    text = " ".join(_instructions())
    assert "sh.rustup.rs" not in text
    assert "static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/" in text
