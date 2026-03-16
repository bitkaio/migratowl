"""Tests for registry query functions."""

import asyncio
import json

import httpx
import pytest

from migratowl.models.schemas import Dependency, Ecosystem

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _dep(name: str, version: str, ecosystem: Ecosystem, manifest: str = "requirements.txt") -> Dependency:
    return Dependency(name=name, current_version=version, ecosystem=ecosystem, manifest_path=manifest)


def _mock_transport(responses: dict[str, httpx.Response]) -> httpx.MockTransport:
    """Return a MockTransport that maps URL paths to canned responses."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in responses:
            return responses[path]
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


# ===========================================================================
# _is_outdated
# ===========================================================================


class TestIsOutdated:
    def test_newer_version_is_outdated(self) -> None:
        from migratowl.registry import _is_outdated

        assert _is_outdated("2.31.0", "2.32.0") is True

    def test_equal_versions_not_outdated(self) -> None:
        from migratowl.registry import _is_outdated

        assert _is_outdated("2.31.0", "2.31.0") is False

    def test_older_latest_not_outdated(self) -> None:
        from migratowl.registry import _is_outdated

        assert _is_outdated("3.0.0", "2.31.0") is False

    def test_range_prefix_stripped(self) -> None:
        from migratowl.registry import _is_outdated

        assert _is_outdated(">=2.28", "2.32.0") is True

    def test_caret_prefix_stripped(self) -> None:
        from migratowl.registry import _is_outdated

        assert _is_outdated("^4.18.0", "4.21.0") is True

    def test_tilde_prefix_stripped(self) -> None:
        from migratowl.registry import _is_outdated

        assert _is_outdated("~1.2.0", "1.3.0") is True

    def test_v_prefix_stripped(self) -> None:
        from migratowl.registry import _is_outdated

        assert _is_outdated("v1.9.0", "v1.10.0") is True

    def test_unparseable_empty_string(self) -> None:
        from migratowl.registry import _is_outdated

        assert _is_outdated("", "2.0.0") is False

    def test_unparseable_star(self) -> None:
        from migratowl.registry import _is_outdated

        assert _is_outdated("*", "2.0.0") is False

    def test_range_with_comma(self) -> None:
        from migratowl.registry import _is_outdated

        assert _is_outdated(">=2.28,<3.0", "2.32.0") is True


# ===========================================================================
# query_pypi
# ===========================================================================


class TestQueryPypi:
    async def test_outdated_returns_outdated_dependency(self) -> None:
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json={
                "info": {
                    "version": "2.32.0",
                    "home_page": "https://requests.readthedocs.io",
                    "project_urls": {
                        "Repository": "https://github.com/psf/requests",
                        "Changelog": "https://github.com/psf/requests/blob/main/HISTORY.md",
                    },
                },
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("requests", "2.31.0", Ecosystem.PYTHON)
            result = await query_pypi(client, dep)

        assert result is not None
        assert result.name == "requests"
        assert result.latest_version == "2.32.0"
        assert result.repository_url == "https://github.com/psf/requests"
        assert result.changelog_url == "https://github.com/psf/requests/blob/main/HISTORY.md"
        assert result.homepage_url == "https://requests.readthedocs.io"

    async def test_up_to_date_returns_none(self) -> None:
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json={
                "info": {"version": "2.31.0", "home_page": None, "project_urls": None},
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("requests", "2.31.0", Ecosystem.PYTHON)
            result = await query_pypi(client, dep)

        assert result is None

    async def test_http_404_raises(self) -> None:
        from migratowl.registry import query_pypi

        transport = _mock_transport({})  # no matching route → 404
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("nonexistent", "1.0.0", Ecosystem.PYTHON)
            with pytest.raises(httpx.HTTPStatusError):
                await query_pypi(client, dep)

    async def test_missing_project_urls(self) -> None:
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json={
                "info": {"version": "2.32.0", "home_page": None, "project_urls": None},
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("requests", "2.31.0", Ecosystem.PYTHON)
            result = await query_pypi(client, dep)

        assert result is not None
        assert result.repository_url is None
        assert result.changelog_url is None

    async def test_extras_bracket_stripped(self) -> None:
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json={
                "info": {"version": "2.32.0", "home_page": None, "project_urls": None},
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("requests[security]", "2.31.0", Ecosystem.PYTHON)
            result = await query_pypi(client, dep)

        assert result is not None
        assert result.name == "requests[security]"


# ===========================================================================
# query_npm
# ===========================================================================


class TestQueryNpm:
    async def test_outdated_with_cleaned_repo_url(self) -> None:
        from migratowl.registry import query_npm

        transport = _mock_transport({
            "/express": httpx.Response(200, json={
                "dist-tags": {"latest": "4.21.0"},
                "homepage": "https://expressjs.com",
                "repository": {"url": "git+https://github.com/expressjs/express.git"},
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("express", "^4.18.0", Ecosystem.NODEJS, "package.json")
            result = await query_npm(client, dep)

        assert result is not None
        assert result.latest_version == "4.21.0"
        assert result.repository_url == "https://github.com/expressjs/express"
        assert result.homepage_url == "https://expressjs.com"

    async def test_up_to_date_returns_none(self) -> None:
        from migratowl.registry import query_npm

        transport = _mock_transport({
            "/express": httpx.Response(200, json={
                "dist-tags": {"latest": "4.18.0"},
                "homepage": None,
                "repository": None,
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("express", "4.18.0", Ecosystem.NODEJS, "package.json")
            result = await query_npm(client, dep)

        assert result is None

    async def test_repository_as_string(self) -> None:
        from migratowl.registry import query_npm

        transport = _mock_transport({
            "/express": httpx.Response(200, json={
                "dist-tags": {"latest": "5.0.0"},
                "homepage": None,
                "repository": "https://github.com/expressjs/express",
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("express", "4.18.0", Ecosystem.NODEJS, "package.json")
            result = await query_npm(client, dep)

        assert result is not None
        assert result.repository_url == "https://github.com/expressjs/express"


# ===========================================================================
# query_crates
# ===========================================================================


class TestQueryCrates:
    async def test_outdated_with_metadata(self) -> None:
        from migratowl.registry import query_crates

        transport = _mock_transport({
            "/api/v1/crates/serde": httpx.Response(200, json={
                "crate": {
                    "newest_version": "1.1.0",
                    "homepage": "https://serde.rs",
                    "repository": "https://github.com/serde-rs/serde",
                    "documentation": "https://docs.rs/serde",
                },
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("serde", "1.0.0", Ecosystem.RUST, "Cargo.toml")
            result = await query_crates(client, dep)

        assert result is not None
        assert result.latest_version == "1.1.0"
        assert result.homepage_url == "https://serde.rs"
        assert result.repository_url == "https://github.com/serde-rs/serde"

    async def test_up_to_date_returns_none(self) -> None:
        from migratowl.registry import query_crates

        transport = _mock_transport({
            "/api/v1/crates/serde": httpx.Response(200, json={
                "crate": {
                    "newest_version": "1.0.0",
                    "homepage": None,
                    "repository": None,
                    "documentation": None,
                },
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("serde", "1.0.0", Ecosystem.RUST, "Cargo.toml")
            result = await query_crates(client, dep)

        assert result is None


# ===========================================================================
# query_golang
# ===========================================================================


class TestQueryGolang:
    async def test_outdated_with_github_repo_url(self) -> None:
        from migratowl.registry import query_golang

        transport = _mock_transport({
            "/github.com/gin-gonic/gin/@latest": httpx.Response(200, json={
                "Version": "v1.10.0",
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("github.com/gin-gonic/gin", "v1.9.0", Ecosystem.GO, "go.mod")
            result = await query_golang(client, dep)

        assert result is not None
        assert result.latest_version == "v1.10.0"
        assert result.repository_url == "https://github.com/gin-gonic/gin"

    async def test_up_to_date_returns_none(self) -> None:
        from migratowl.registry import query_golang

        transport = _mock_transport({
            "/github.com/gin-gonic/gin/@latest": httpx.Response(200, json={
                "Version": "v1.9.0",
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("github.com/gin-gonic/gin", "v1.9.0", Ecosystem.GO, "go.mod")
            result = await query_golang(client, dep)

        assert result is None

    async def test_non_github_module_no_url(self) -> None:
        from migratowl.registry import query_golang

        transport = _mock_transport({
            "/example.com/foo/@latest": httpx.Response(200, json={
                "Version": "v2.0.0",
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("example.com/foo", "v1.0.0", Ecosystem.GO, "go.mod")
            result = await query_golang(client, dep)

        assert result is not None
        assert result.repository_url is None


# ===========================================================================
# check_outdated (orchestrator)
# ===========================================================================


class TestCheckOutdated:
    async def test_mixed_ecosystems_returns_only_outdated(self) -> None:
        from migratowl.registry import check_outdated

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json={
                "info": {"version": "2.32.0", "home_page": None, "project_urls": None},
            }),
            "/express": httpx.Response(200, json={
                "dist-tags": {"latest": "4.18.0"},
                "homepage": None,
                "repository": None,
            }),
        })

        deps = [
            _dep("requests", "2.31.0", Ecosystem.PYTHON),
            _dep("express", "4.18.0", Ecosystem.NODEJS, "package.json"),
        ]

        async with httpx.AsyncClient(transport=transport) as client:
            result = await check_outdated(deps, concurrency=5, client=client)

        assert len(result) == 1
        assert result[0].name == "requests"

    async def test_single_failed_query_does_not_block_others(self) -> None:
        from migratowl.registry import check_outdated

        transport = _mock_transport({
            # requests → 404 (will fail)
            "/pypi/flask/json": httpx.Response(200, json={
                "info": {"version": "3.1.0", "home_page": None, "project_urls": None},
            }),
        })

        deps = [
            _dep("requests", "2.31.0", Ecosystem.PYTHON),
            _dep("flask", "3.0.0", Ecosystem.PYTHON),
        ]

        async with httpx.AsyncClient(transport=transport) as client:
            result = await check_outdated(deps, concurrency=5, client=client)

        assert len(result) == 1
        assert result[0].name == "flask"

    async def test_empty_input(self) -> None:
        from migratowl.registry import check_outdated

        async with httpx.AsyncClient(transport=_mock_transport({})) as client:
            result = await check_outdated([], concurrency=5, client=client)

        assert result == []
