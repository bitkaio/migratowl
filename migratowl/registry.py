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

from migratowl.config import get_settings
from migratowl.models.schemas import Dependency, Ecosystem, OutdatedCheckMode, OutdatedDependency

logger = logging.getLogger(__name__)

_USER_AGENT = "migratowl/0.1.0 (https://github.com/bitkaio/migratowl)"


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


def _max_version(versions: list[str], include_prerelease: bool) -> str | None:
    """Return the maximum version string from a list, optionally excluding pre-releases.

    Strips leading 'v' before parsing. Invalid version strings are silently skipped.
    Returns the normalized PEP 440 string of the maximum version, or None if the
    list is empty or all entries are invalid/excluded.
    """
    parsed: list[Version] = []
    for raw in versions:
        try:
            ver = Version(raw.lstrip("v"))
        except InvalidVersion:
            continue
        if not include_prerelease and ver.is_prerelease:
            continue
        parsed.append(ver)
    if not parsed:
        return None
    return str(max(parsed))


def _resolve_latest(
    current_version: str,
    all_versions: list[str],
    options: CheckOptions,
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
            return _max_version(candidates, options.include_prerelease)
        # Bare/exact version: fall through to global max (nothing to constrain)
        return _max_version(all_versions, options.include_prerelease)
    # NORMAL: global max, ignore constraint
    return _max_version(all_versions, options.include_prerelease)


def _is_outdated(current: str, latest: str) -> bool:
    """Return True if latest is strictly newer than current."""
    try:
        cur = Version(_clean_version(current))
        lat = Version(_clean_version(latest))
    except InvalidVersion:
        return False
    return lat > cur


def _extract_pypi_url(project_urls: dict[str, str] | None, keys: list[str]) -> str | None:
    """Extract first matching URL from PyPI project_urls."""
    if not project_urls:
        return None
    for key in keys:
        for k, v in project_urls.items():
            if k.lower() == key.lower():
                return v
    return None


def _extract_npm_repo_url(repository: dict[str, Any] | str | None) -> str | None:
    """Clean npm repository URL — strip git+ prefix and .git suffix."""
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
    if url.startswith("git+"):
        url = url[4:]
    if url.endswith(".git"):
        url = url[:-4]
    return url


_KNOWN_GO_HOSTS = ("github.com", "gitlab.com", "bitbucket.org")


def _go_proxy_encode(module_path: str) -> str:
    """Encode a Go module path for the module proxy URL.

    The Go module proxy spec requires uppercase letters to be escaped as
    ``!lowercase`` (e.g. ``Masterminds`` → ``!masterminds``) so that paths
    remain unambiguous on case-insensitive file systems.
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

    all_versions = list(data.get("releases", {}).keys())
    target = _resolve_latest(dep.current_version, all_versions, options)

    if target is None or not _is_outdated(dep.current_version, target):
        return None

    project_urls = info.get("project_urls")
    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=target,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
        homepage_url=info.get("home_page") or None,
        repository_url=_extract_pypi_url(project_urls, ["Repository", "Source", "Source Code", "GitHub"]),
        changelog_url=_extract_pypi_url(project_urls, ["Changelog", "Changes", "Release Notes", "History"]),
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
    target = _resolve_latest(dep.current_version, all_versions, options)

    if target is None or not _is_outdated(dep.current_version, target):
        return None

    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=target,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
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
    target = _resolve_latest(dep.current_version, all_versions, options)

    if target is None or not _is_outdated(dep.current_version, target):
        return None

    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=target,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
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

    target = _resolve_latest(dep.current_version, all_versions, options)

    if target is None or not _is_outdated(dep.current_version, target):
        return None

    # Re-attach 'v' prefix that packaging normalizes away, if the original had it
    if not target.startswith("v") and any(v.startswith("v") for v in all_versions):
        target = f"v{target}"

    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=target,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
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

    if target is None or not _is_outdated(dep.current_version, target):
        return None

    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=target,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
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
) -> list[OutdatedDependency]:
    """Check a list of dependencies against their package registries.

    Returns only the outdated ones. Failed queries are logged and skipped.
    """
    if not deps:
        return []

    _options = options if options is not None else CheckOptions()
    sem = asyncio.Semaphore(concurrency)

    async def _query_one(c: httpx.AsyncClient, dep: Dependency) -> OutdatedDependency | None:
        query_fn = _ECOSYSTEM_QUERIES.get(dep.ecosystem)
        if query_fn is None:
            logger.warning("No registry query for ecosystem %s", dep.ecosystem)
            return None
        async with sem:
            try:
                return await query_fn(c, dep, _options)
            except Exception:
                logger.warning("Failed to query registry for %s (%s)", dep.name, dep.ecosystem, exc_info=True)
                return None

    owns_client = client is None
    if owns_client:
        settings = get_settings()
        client = httpx.AsyncClient(
            timeout=settings.http_timeout,
            headers={"User-Agent": _USER_AGENT},
        )

    assert client is not None  # always assigned: either passed in or created above
    try:
        results = await asyncio.gather(*[_query_one(client, dep) for dep in deps])
    finally:
        if owns_client:
            await client.aclose()

    return [r for r in results if r is not None]
