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

"""Query package registries for latest versions and metadata."""

import asyncio
import logging
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import httpx
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from migratowl.http import get_http_client
from migratowl.models.schemas import Dependency, Ecosystem, OutdatedCheckMode, OutdatedDependency, RegistryFailure

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------------------
# Check options
# ---------------------------------------------------------------------------


@dataclass
class CheckOptions:
    """Configuration for how the latest available version is resolved.

    mode: SAFE  — respect the declared semver constraint; only flag if a newer
                  version exists *within* the declared range.
          NORMAL — ignore the constraint; compare bare version against the
                   globally highest published version.
    include_prerelease: when True, pre-release versions (alpha/beta/rc) are
                        considered when picking the latest version.
    """

    mode: OutdatedCheckMode = field(default=OutdatedCheckMode.NORMAL)
    include_prerelease: bool = False
    # Python version the sandbox installs with; PyPI releases whose
    # requires_python excludes it are skipped. None = no filtering.
    python_version: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_RANGE_PREFIX_RE = re.compile(r"^[>=<^~!]*")


def _clean_version(raw: str) -> str:
    """Strip range prefixes, take first segment before comma, strip 'v'."""
    raw = raw.split(",")[0].strip()
    raw = _RANGE_PREFIX_RE.sub("", raw)
    raw = raw.lstrip("v")
    return raw.strip()


def _constraint_to_specifier(raw: str) -> SpecifierSet | None:
    """Convert a declared version string into a SpecifierSet for safe-mode filtering.

    Returns None for bare/exact versions (no range to filter within).

    Handles:
      - npm/Cargo caret:  ^4.21.2  →  >=4.21.2,<5.0.0  (0.x/0.0.x special cases)
      - npm/Cargo tilde:  ~4.21.2  →  >=4.21.2,<4.22.0
      - Python operators: >=4.0.0,<5.0.0  (passed through to SpecifierSet directly)
      - Bare / exact (=): returns None

    Edge cases:
      - Empty string or ``*``: returns None (no constraint to apply).
      - Malformed caret/tilde (non-numeric segments): returns None.
      - Python-style operator strings that are invalid SpecifierSet syntax
        (e.g. ``>=foo``): returns None.
    """
    raw = raw.strip()
    if not raw or raw == "*":
        return None

    # Caret operator — npm/Cargo compatible-release semantics
    if raw.startswith("^"):
        base = _clean_version(raw)
        try:
            parts = [int(p) for p in base.split(".")[:3]]
        except ValueError:
            return None
        major, minor, patch = (parts + [0, 0])[:3]
        if major != 0:
            return SpecifierSet(f">={base},<{major + 1}.0.0")
        if minor != 0:
            return SpecifierSet(f">={base},<{major}.{minor + 1}.0")
        return SpecifierSet(f">={base},<{major}.{minor}.{patch + 1}")

    # Tilde operator — npm/Cargo patch-level compatible
    if raw.startswith("~") and not raw.startswith("~="):
        base = _clean_version(raw)
        try:
            parts = [int(p) for p in base.split(".")[:2]]
        except ValueError:
            return None
        major, minor = (parts + [0])[:2]
        return SpecifierSet(f">={base},<{major}.{minor + 1}.0")

    # Python-style operators (>=, <=, >, <, ==, !=, ~=) — pass through
    if re.match(r"^[><=!~]", raw):
        try:
            return SpecifierSet(raw)
        except InvalidSpecifier:
            return None

    # Bare version or single = (exact pin) → no range
    return None


# Semver as used by npm, crates.io and Go: MAJOR.MINOR.PATCH[-prerelease][+build]
_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")

# Release sorts after all its prereleases: (1,) > (0, ...)
_RELEASE = (1,)


def _semver_key(raw: str) -> tuple | None:
    """Semver precedence key, or None when ``raw`` is not strict semver.

    Prerelease identifiers compare per the semver spec: numeric ones
    numerically and below alphanumeric ones (``beta.2 < beta.10 < beta.x``).
    """
    m = _SEMVER_RE.match(raw.strip())
    if not m:
        return None
    pre = m[4]
    pre_key = _RELEASE if pre is None else (
        0, tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in pre.split("."))
    )
    return (int(m[1]), int(m[2]), int(m[3])), pre_key


def _sort_key(raw: str, semver: bool) -> tuple[object, bool] | None:
    """``(sort key, is_prerelease)`` for a version string, or None if unparseable.

    ``semver=True`` reads ``-x`` suffixes as prereleases (``5.0.0-0`` is before
    5.0.0); PEP 440 would read them as post-releases. Non-semver strings in a
    semver ecosystem (e.g. ``2.1``) fall back to PEP 440 in the same key shape.
    """
    if semver:
        key = _semver_key(raw)
        if key is not None:
            return key, key[1] != _RELEASE
    try:
        ver = Version(raw.strip().lstrip("v"))
    except InvalidVersion:
        return None
    if not semver:
        return ver, ver.is_prerelease
    release = tuple(ver.release) + (0,) * max(0, 3 - len(ver.release))
    return (release, (0, ()) if ver.is_prerelease else _RELEASE), ver.is_prerelease


def _max_version(versions: list[str], include_prerelease: bool, *, semver: bool = False) -> str | None:
    """Return the highest version from a list, optionally excluding pre-releases.

    Returns the version as the registry spells it (only a leading 'v' is
    stripped), so it can be passed back to npm/cargo/go/pip unchanged. Invalid
    version strings are skipped; returns None if nothing is left.
    """
    best: tuple[object, str] | None = None
    for raw in versions:
        parsed = _sort_key(raw, semver)
        if parsed is None:
            continue
        key, is_pre = parsed
        if is_pre and not include_prerelease:
            continue
        if best is None or key > best[0]:  # type: ignore[operator]
            best = (key, raw)
    return best[1].strip().lstrip("v") if best else None


def _resolve_latest(
    current_version: str,
    all_versions: list[str],
    options: CheckOptions,
    *,
    semver: bool = False,
) -> str | None:
    """Return the target version to compare against given the mode and options.

    SAFE:   filter all_versions to those satisfying the declared constraint,
            then return the max of that filtered list.
    NORMAL: return the global max of all_versions (ignoring the constraint).

    Returns None when no suitable version is found.
    """
    if options.mode == OutdatedCheckMode.SAFE:
        specifier = _constraint_to_specifier(current_version)
        if specifier is not None:
            candidates = []
            for v in all_versions:
                cleaned = _clean_version(v)
                try:
                    if cleaned in specifier:
                        candidates.append(v)
                except InvalidVersion:
                    continue
            return _max_version(candidates, options.include_prerelease, semver=semver)
        # Bare/exact version: fall through to global max (nothing to constrain)
        return _max_version(all_versions, options.include_prerelease, semver=semver)
    # NORMAL: global max, ignore constraint
    return _max_version(all_versions, options.include_prerelease, semver=semver)


def _is_outdated(current: str, latest: str, *, semver: bool = False) -> bool:
    """Return True if latest is strictly newer than current."""
    cur = _sort_key(_clean_version(current), semver)
    lat = _sort_key(_clean_version(latest), semver)
    if cur is None or lat is None:
        return False
    return lat[0] > cur[0]  # type: ignore[operator]


def _supports_python(files: list[dict[str, Any]], python_version: str | None) -> bool:
    """False only when every file's ``requires_python`` excludes ``python_version``."""
    if python_version is None or not files:
        return True
    for f in files:
        requires = f.get("requires_python")
        if not requires:
            return True
        try:
            if Version(python_version) in SpecifierSet(requires):
                return True
        except (InvalidSpecifier, InvalidVersion):
            return True
    return False


def _extract_url_by_key(project_urls: dict[str, str] | None, keys: list[str]) -> str | None:
    """Extract first matching URL from a project_urls dict (case-insensitive key lookup)."""
    if not project_urls:
        return None
    for key in keys:
        for k, v in project_urls.items():
            if k.lower() == key.lower():
                return v
    return None


def _clean_git_url(url: str) -> str:
    """Strip ``git+`` prefix and ``.git`` suffix from a repository URL."""
    if url.startswith("git+"):
        url = url[4:]
    if url.endswith(".git"):
        url = url[:-4]
    return url


def _extract_npm_repo_url(repository: dict[str, Any] | str | None) -> str | None:
    """Extract and clean npm repository URL from the ``repository`` field."""
    if repository is None:
        return None
    if isinstance(repository, str):
        url = repository
    elif isinstance(repository, dict):
        url = repository.get("url", "")
    else:
        return None
    if not url:
        return None
    return _clean_git_url(url)


_KNOWN_GO_HOSTS = ("github.com", "gitlab.com", "bitbucket.org")


def _go_proxy_encode(module_path: str) -> str:
    """Encode a Go module path for the module proxy URL.

    The Go module proxy spec requires uppercase letters to be escaped as
    ``!lowercase`` (e.g. ``Masterminds`` → ``!masterminds``) so that paths
    remain unambiguous on case-insensitive file systems.
    Module paths without uppercase letters are returned unmodified.
    """
    return re.sub(r"[A-Z]", lambda m: "!" + m.group(0).lower(), module_path)


def _go_module_to_repo_url(module_path: str) -> str | None:
    """Derive repository URL from Go module path for known hosts."""
    for host in _KNOWN_GO_HOSTS:
        if module_path.startswith(host + "/"):
            parts = module_path.split("/")
            if len(parts) >= 3:
                return f"https://{parts[0]}/{parts[1]}/{parts[2]}"
    return None


# ---------------------------------------------------------------------------
# Per-ecosystem query functions
# ---------------------------------------------------------------------------


_DEFAULT_OPTIONS = CheckOptions()


async def query_pypi(
    client: httpx.AsyncClient,
    dep: Dependency,
    options: CheckOptions = _DEFAULT_OPTIONS,
) -> OutdatedDependency | None:
    """Query PyPI for latest version of a Python package."""
    name = dep.name.split("[")[0]  # strip extras
    resp = await client.get(f"https://pypi.org/pypi/{name}/json")
    resp.raise_for_status()
    data = resp.json()
    info = data["info"]

    # PEP 592: a release whose files are all yanked was withdrawn — never suggest it.
    # A release the sandbox's Python cannot install would only fail the install step.
    all_versions = [
        version
        for version, files in data.get("releases", {}).items()
        if (not files or not all(f.get("yanked", False) for f in files))
        and _supports_python(files, options.python_version)
    ]
    target = _resolve_latest(dep.current_version, all_versions, options)

    if target is None or not _is_outdated(dep.installed_version or dep.current_version, target):
        return None

    project_urls = info.get("project_urls")
    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=target,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
        installed_version=dep.installed_version,
        homepage_url=info.get("home_page") or None,
        repository_url=_extract_url_by_key(project_urls, ["Repository", "Source", "Source Code", "GitHub"]),
        changelog_url=_extract_url_by_key(project_urls, ["Changelog", "Changes", "Release Notes", "History"]),
    )


async def query_npm(
    client: httpx.AsyncClient,
    dep: Dependency,
    options: CheckOptions = _DEFAULT_OPTIONS,
) -> OutdatedDependency | None:
    """Query npm registry for latest version of a Node.js package."""
    resp = await client.get(f"https://registry.npmjs.org/{dep.name}")
    resp.raise_for_status()
    data = resp.json()

    all_versions = list(data.get("versions", {}).keys())
    target = _resolve_latest(dep.current_version, all_versions, options, semver=True)

    if target is None or not _is_outdated(dep.installed_version or dep.current_version, target, semver=True):
        return None

    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=target,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
        installed_version=dep.installed_version,
        homepage_url=data.get("homepage") or None,
        repository_url=_extract_npm_repo_url(data.get("repository")),
    )


async def query_crates(
    client: httpx.AsyncClient,
    dep: Dependency,
    options: CheckOptions = _DEFAULT_OPTIONS,
) -> OutdatedDependency | None:
    """Query crates.io for latest version of a Rust crate."""
    resp = await client.get(f"https://crates.io/api/v1/crates/{dep.name}")
    resp.raise_for_status()
    data = resp.json()
    crate = data["crate"]

    all_versions = [v["num"] for v in data.get("versions", []) if not v.get("yanked", False)]
    target = _resolve_latest(dep.current_version, all_versions, options, semver=True)

    if target is None or not _is_outdated(dep.installed_version or dep.current_version, target, semver=True):
        return None

    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=target,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
        installed_version=dep.installed_version,
        homepage_url=crate.get("homepage") or None,
        repository_url=crate.get("repository") or None,
        changelog_url=crate.get("documentation") or None,
    )


async def query_golang(
    client: httpx.AsyncClient,
    dep: Dependency,
    options: CheckOptions = _DEFAULT_OPTIONS,
) -> OutdatedDependency | None:
    """Query Go module proxy for latest version."""
    encoded = _go_proxy_encode(dep.name)
    resp = await client.get(f"https://proxy.golang.org/{encoded}/@v/list")
    resp.raise_for_status()
    all_versions = [v for v in resp.text.splitlines() if v.strip()]

    target = _resolve_latest(dep.current_version, all_versions, options, semver=True)

    if target is None or not _is_outdated(dep.installed_version or dep.current_version, target, semver=True):
        return None

    # Re-attach 'v' prefix that packaging normalizes away.
    # Prefer the current_version's own style; fall back to registry majority.
    prefixed_count = sum(1 for v in all_versions if v.startswith("v"))
    unprefixed_count = len(all_versions) - prefixed_count
    prefers_v_prefix = dep.current_version.startswith("v") or (prefixed_count > unprefixed_count)
    if prefers_v_prefix and not target.startswith("v"):
        target = f"v{target}"

    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=target,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
        installed_version=dep.installed_version,
        repository_url=_go_module_to_repo_url(dep.name),
    )


async def query_maven_central(
    client: httpx.AsyncClient,
    dep: Dependency,
    options: CheckOptions = _DEFAULT_OPTIONS,
) -> OutdatedDependency | None:
    """Query Maven Central Search API for latest version of a Java package.

    Expects dep.name in ``groupId:artifactId`` format.
    Uses core=gav to retrieve all available versions in one request.
    """
    if ":" not in dep.name:
        return None
    group_id, artifact_id = dep.name.split(":", 1)
    url = (
        f"https://search.maven.org/solrsearch/select"
        f"?q=g:{group_id}+AND+a:{artifact_id}&core=gav&rows=100&wt=json"
    )
    resp = await client.get(url)
    resp.raise_for_status()
    docs = resp.json()["response"]["docs"]
    if not docs:
        return None

    all_versions = [d["v"] for d in docs if "v" in d]
    target = _resolve_latest(dep.current_version, all_versions, options)

    if target is None or not _is_outdated(dep.installed_version or dep.current_version, target):
        return None

    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=target,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
        installed_version=dep.installed_version,
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_ECOSYSTEM_QUERIES: dict[
    Ecosystem,
    Callable[[httpx.AsyncClient, Dependency, CheckOptions], Coroutine[Any, Any, OutdatedDependency | None]],
] = {
    Ecosystem.PYTHON: query_pypi,
    Ecosystem.NODEJS: query_npm,
    Ecosystem.RUST: query_crates,
    Ecosystem.GO: query_golang,
    Ecosystem.JAVA: query_maven_central,
}


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------


async def check_outdated(
    deps: list[Dependency],
    options: CheckOptions | None = None,
    concurrency: int = 10,
    client: httpx.AsyncClient | None = None,
) -> tuple[list[OutdatedDependency], list[RegistryFailure]]:
    """Check a list of dependencies against their package registries.

    Returns a tuple of (outdated, failures). Outdated contains deps with newer
    versions available. Failures contains deps whose registry query raised an
    exception (network errors, unexpected response shapes, etc.).
    """
    if not deps:
        return [], []

    _options = options if options is not None else CheckOptions()
    sem = asyncio.Semaphore(concurrency)

    async def _query_one(
        c: httpx.AsyncClient, dep: Dependency
    ) -> tuple[OutdatedDependency | None, RegistryFailure | None]:
        query_fn = _ECOSYSTEM_QUERIES.get(dep.ecosystem)
        if query_fn is None:
            logger.warning("No registry query for ecosystem %s", dep.ecosystem)
            return None, None
        async with sem:
            try:
                result = await query_fn(c, dep, _options)
                return result, None
            except Exception:
                logger.warning("Failed to query registry for %s (%s)", dep.name, dep.ecosystem, exc_info=True)
                return None, RegistryFailure(name=dep.name, ecosystem=dep.ecosystem)

    # The shared client retries 429/5xx with backoff and sends Migratowl's User-Agent.
    http = client if client is not None else get_http_client()
    pairs: list[tuple[OutdatedDependency | None, RegistryFailure | None]] = list(
        await asyncio.gather(*[_query_one(http, dep) for dep in deps])
    )

    outdated = [o for o, _ in pairs if o is not None]
    failures = [f for _, f in pairs if f is not None]
    return outdated, failures