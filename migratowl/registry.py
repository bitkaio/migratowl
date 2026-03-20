"""Query package registries for latest versions and metadata."""

import asyncio
import logging
import re
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version

from migratowl.config import get_settings
from migratowl.models.schemas import Dependency, Ecosystem, OutdatedDependency

logger = logging.getLogger(__name__)

_USER_AGENT = "migratowl/0.1.0 (https://github.com/bitkaio/migratowl)"

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


async def query_pypi(client: httpx.AsyncClient, dep: Dependency) -> OutdatedDependency | None:
    """Query PyPI for latest version of a Python package."""
    name = dep.name.split("[")[0]  # strip extras
    resp = await client.get(f"https://pypi.org/pypi/{name}/json")
    resp.raise_for_status()
    data = resp.json()
    info = data["info"]
    latest = info["version"]

    if not _is_outdated(dep.current_version, latest):
        return None

    project_urls = info.get("project_urls")
    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=latest,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
        homepage_url=info.get("home_page") or None,
        repository_url=_extract_pypi_url(project_urls, ["Repository", "Source", "Source Code", "GitHub"]),
        changelog_url=_extract_pypi_url(project_urls, ["Changelog", "Changes", "Release Notes", "History"]),
    )


async def query_npm(client: httpx.AsyncClient, dep: Dependency) -> OutdatedDependency | None:
    """Query npm registry for latest version of a Node.js package."""
    resp = await client.get(f"https://registry.npmjs.org/{dep.name}")
    resp.raise_for_status()
    data = resp.json()
    latest = data["dist-tags"]["latest"]

    if not _is_outdated(dep.current_version, latest):
        return None

    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=latest,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
        homepage_url=data.get("homepage") or None,
        repository_url=_extract_npm_repo_url(data.get("repository")),
    )


async def query_crates(client: httpx.AsyncClient, dep: Dependency) -> OutdatedDependency | None:
    """Query crates.io for latest version of a Rust crate."""
    resp = await client.get(f"https://crates.io/api/v1/crates/{dep.name}")
    resp.raise_for_status()
    crate = resp.json()["crate"]
    latest = crate["newest_version"]

    if not _is_outdated(dep.current_version, latest):
        return None

    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=latest,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
        homepage_url=crate.get("homepage") or None,
        repository_url=crate.get("repository") or None,
        changelog_url=crate.get("documentation") or None,
    )


async def query_golang(client: httpx.AsyncClient, dep: Dependency) -> OutdatedDependency | None:
    """Query Go module proxy for latest version."""
    resp = await client.get(f"https://proxy.golang.org/{_go_proxy_encode(dep.name)}/@latest")
    resp.raise_for_status()
    data = resp.json()
    latest = data["Version"]

    if not _is_outdated(dep.current_version, latest):
        return None

    return OutdatedDependency(
        name=dep.name,
        current_version=dep.current_version,
        latest_version=latest,
        ecosystem=dep.ecosystem,
        manifest_path=dep.manifest_path,
        repository_url=_go_module_to_repo_url(dep.name),
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_ECOSYSTEM_QUERIES: dict[
    Ecosystem,
    Callable[[httpx.AsyncClient, Dependency], Coroutine[Any, Any, OutdatedDependency | None]],
] = {
    Ecosystem.PYTHON: query_pypi,
    Ecosystem.NODEJS: query_npm,
    Ecosystem.RUST: query_crates,
    Ecosystem.GO: query_golang,
}


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------


async def check_outdated(
    deps: list[Dependency],
    concurrency: int = 10,
    client: httpx.AsyncClient | None = None,
) -> list[OutdatedDependency]:
    """Check a list of dependencies against their package registries.

    Returns only the outdated ones. Failed queries are logged and skipped.
    """
    if not deps:
        return []

    sem = asyncio.Semaphore(concurrency)

    async def _query_one(c: httpx.AsyncClient, dep: Dependency) -> OutdatedDependency | None:
        query_fn = _ECOSYSTEM_QUERIES.get(dep.ecosystem)
        if query_fn is None:
            logger.warning("No registry query for ecosystem %s", dep.ecosystem)
            return None
        async with sem:
            try:
                return await query_fn(c, dep)
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

    try:
        results = await asyncio.gather(*[_query_one(client, dep) for dep in deps])
    finally:
        if owns_client:
            await client.aclose()

    return [r for r in results if r is not None]
