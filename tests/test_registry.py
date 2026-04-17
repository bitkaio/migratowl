"""Tests for registry query functions."""


import httpx
import pytest

from migratowl.models.schemas import Dependency, Ecosystem, OutdatedCheckMode
from migratowl.registry import CheckOptions

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
                "releases": {"2.31.0": [], "2.32.0": []},
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
                "releases": {"2.31.0": []},
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
                "releases": {"2.31.0": [], "2.32.0": []},
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
                "releases": {"2.31.0": [], "2.32.0": []},
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
                "versions": {"4.18.0": {}, "4.21.0": {}},
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
                "versions": {"4.18.0": {}},
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
                "versions": {"4.18.0": {}, "5.0.0": {}},
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
                "versions": [
                    {"num": "1.0.0", "yanked": False},
                    {"num": "1.1.0", "yanked": False},
                ],
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
                "versions": [{"num": "1.0.0", "yanked": False}],
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
            "/github.com/gin-gonic/gin/@v/list": httpx.Response(200, text="v1.9.0\nv1.10.0\n"),
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
            "/github.com/gin-gonic/gin/@v/list": httpx.Response(200, text="v1.9.0\n"),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("github.com/gin-gonic/gin", "v1.9.0", Ecosystem.GO, "go.mod")
            result = await query_golang(client, dep)

        assert result is None

    async def test_non_github_module_no_url(self) -> None:
        from migratowl.registry import query_golang

        transport = _mock_transport({
            "/example.com/foo/@v/list": httpx.Response(200, text="v1.0.0\nv2.0.0\n"),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("example.com/foo", "v1.0.0", Ecosystem.GO, "go.mod")
            result = await query_golang(client, dep)

        assert result is not None
        assert result.repository_url is None


class TestGoProxyEncode:
    def test_lowercase_unchanged(self) -> None:
        from migratowl.registry import _go_proxy_encode

        assert _go_proxy_encode("github.com/gin-gonic/gin") == "github.com/gin-gonic/gin"

    def test_uppercase_letter_encoded(self) -> None:
        from migratowl.registry import _go_proxy_encode

        assert _go_proxy_encode("github.com/Masterminds/squirrel") == "github.com/!masterminds/squirrel"

    def test_multiple_uppercase_encoded(self) -> None:
        from migratowl.registry import _go_proxy_encode

        assert _go_proxy_encode("github.com/BurntSushi/toml") == "github.com/!burnt!sushi/toml"


class TestQueryGolangCaseEncoding:
    async def test_uppercase_module_uses_encoded_url(self) -> None:
        from migratowl.registry import query_golang

        transport = _mock_transport({
            "/github.com/!masterminds/squirrel/@v/list": httpx.Response(200, text="v1.4.0\nv1.5.0\n"),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("github.com/Masterminds/squirrel", "v1.4.0", Ecosystem.GO, "go.mod")
            result = await query_golang(client, dep)

        assert result is not None
        assert result.latest_version == "v1.5.0"


# ===========================================================================
# check_outdated (orchestrator)
# ===========================================================================


class TestCheckOutdated:
    async def test_mixed_ecosystems_returns_only_outdated(self) -> None:
        from migratowl.registry import check_outdated

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json={
                "info": {"version": "2.32.0", "home_page": None, "project_urls": None},
                "releases": {"2.31.0": [], "2.32.0": []},
            }),
            "/express": httpx.Response(200, json={
                "dist-tags": {"latest": "4.18.0"},
                "homepage": None,
                "repository": None,
                "versions": {"4.18.0": {}},
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
                "releases": {"3.0.0": [], "3.1.0": []},
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


# ===========================================================================
# _constraint_to_specifier
# ===========================================================================


class TestConstraintToSpecifier:
    def test_caret_normal_major(self) -> None:
        from migratowl.registry import _constraint_to_specifier

        spec = _constraint_to_specifier("^4.21.2")
        assert spec is not None
        assert "4.21.2" in spec   # lower bound is inclusive
        assert "4.99.0" in spec
        assert "5.0.0" not in spec

    def test_caret_zero_major(self) -> None:
        from migratowl.registry import _constraint_to_specifier

        spec = _constraint_to_specifier("^0.4.2")
        assert spec is not None
        assert "0.4.2" in spec
        assert "0.4.9" in spec
        assert "0.5.0" not in spec
        assert "1.0.0" not in spec

    def test_caret_zero_minor(self) -> None:
        from migratowl.registry import _constraint_to_specifier

        spec = _constraint_to_specifier("^0.0.3")
        assert spec is not None
        assert "0.0.3" in spec
        assert "0.0.4" not in spec

    def test_tilde_npm_style(self) -> None:
        from migratowl.registry import _constraint_to_specifier

        spec = _constraint_to_specifier("~4.21.2")
        assert spec is not None
        assert "4.21.9" in spec
        assert "4.22.0" not in spec

    def test_python_ge_operator(self) -> None:
        from migratowl.registry import _constraint_to_specifier

        spec = _constraint_to_specifier(">=4.0.0")
        assert spec is not None
        assert "4.0.0" in spec
        assert "5.0.0" in spec
        assert "3.9.9" not in spec

    def test_python_multi_segment(self) -> None:
        from migratowl.registry import _constraint_to_specifier

        spec = _constraint_to_specifier(">=4.0.0,<5.0.0")
        assert spec is not None
        assert "4.9.9" in spec
        assert "5.0.0" not in spec

    def test_bare_version_returns_none(self) -> None:
        from migratowl.registry import _constraint_to_specifier

        assert _constraint_to_specifier("4.21.2") is None

    def test_exact_equals_returns_none(self) -> None:
        from migratowl.registry import _constraint_to_specifier

        assert _constraint_to_specifier("=4.21.2") is None

    def test_wildcard_returns_none(self) -> None:
        from migratowl.registry import _constraint_to_specifier

        assert _constraint_to_specifier("*") is None

    def test_empty_returns_none(self) -> None:
        from migratowl.registry import _constraint_to_specifier

        assert _constraint_to_specifier("") is None


# ===========================================================================
# _max_version
# ===========================================================================


class TestMaxVersion:
    def test_returns_highest_stable(self) -> None:
        from migratowl.registry import _max_version

        result = _max_version(["1.0.0", "2.0.0", "1.9.0"], include_prerelease=False)
        assert result == "2.0.0"

    def test_excludes_prerelease_when_flag_false(self) -> None:
        from migratowl.registry import _max_version

        result = _max_version(["1.0.0", "2.0.0b1", "1.9.0"], include_prerelease=False)
        assert result == "1.9.0"

    def test_includes_prerelease_when_flag_true(self) -> None:
        from migratowl.registry import _max_version

        result = _max_version(["1.0.0", "2.0.0b1", "1.9.0"], include_prerelease=True)
        assert result == "2.0.0b1"

    def test_npm_style_prerelease_excluded(self) -> None:
        from migratowl.registry import _max_version

        # npm uses dash-separated pre-release labels
        result = _max_version(["4.21.2", "5.0.0-beta.3", "4.22.0"], include_prerelease=False)
        assert result == "4.22.0"

    def test_npm_style_prerelease_included(self) -> None:
        from migratowl.registry import _max_version

        result = _max_version(["4.21.2", "5.0.0-beta.3", "4.22.0"], include_prerelease=True)
        # 5.0.0b3 > 4.22.0
        assert result is not None
        from packaging.version import Version
        assert Version(result) > Version("4.22.0")

    def test_v_prefix_stripped(self) -> None:
        from migratowl.registry import _max_version

        result = _max_version(["v1.0.0", "v2.0.0", "v1.9.0"], include_prerelease=False)
        assert result == "2.0.0"

    def test_empty_list_returns_none(self) -> None:
        from migratowl.registry import _max_version

        assert _max_version([], include_prerelease=False) is None

    def test_all_invalid_returns_none(self) -> None:
        from migratowl.registry import _max_version

        assert _max_version(["not-a-version", "also-bad"], include_prerelease=False) is None

    def test_single_version(self) -> None:
        from migratowl.registry import _max_version

        assert _max_version(["3.1.4"], include_prerelease=False) == "3.1.4"


# ===========================================================================
# Mode-aware query_npm
# ===========================================================================


class TestQueryNpmModes:
    def _packument(self, versions: list[str], latest: str) -> dict:
        return {
            "dist-tags": {"latest": latest},
            "homepage": "https://expressjs.com",
            "repository": {"url": "git+https://github.com/expressjs/express.git"},
            "versions": {v: {} for v in versions},
        }

    async def test_safe_mode_not_outdated_when_latest_4x_is_current(self) -> None:
        """^4.21.2 in safe mode: 4.21.2 is max within constraint → not outdated."""
        from migratowl.registry import query_npm

        transport = _mock_transport({
            "/express": httpx.Response(200, json=self._packument(
                versions=["4.20.0", "4.21.2", "5.0.0"],
                latest="4.21.2",
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.SAFE, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("express", "^4.21.2", Ecosystem.NODEJS, "package.json")
            result = await query_npm(client, dep, opts)

        assert result is None

    async def test_safe_mode_outdated_when_newer_minor_exists(self) -> None:
        """^4.18.0 in safe mode: 4.21.2 available within constraint → outdated."""
        from migratowl.registry import query_npm

        transport = _mock_transport({
            "/express": httpx.Response(200, json=self._packument(
                versions=["4.18.0", "4.21.2", "5.0.0"],
                latest="4.21.2",
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.SAFE, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("express", "^4.18.0", Ecosystem.NODEJS, "package.json")
            result = await query_npm(client, dep, opts)

        assert result is not None
        assert result.latest_version == "4.21.2"

    async def test_normal_mode_flags_major_bump(self) -> None:
        """^4.21.2 in normal mode: 5.x exists → outdated."""
        from migratowl.registry import query_npm

        transport = _mock_transport({
            "/express": httpx.Response(200, json=self._packument(
                versions=["4.20.0", "4.21.2", "5.0.0"],
                latest="4.21.2",
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("express", "^4.21.2", Ecosystem.NODEJS, "package.json")
            result = await query_npm(client, dep, opts)

        assert result is not None
        assert result.latest_version == "5.0.0"

    async def test_normal_mode_excludes_prerelease_by_default(self) -> None:
        from migratowl.registry import query_npm

        transport = _mock_transport({
            "/express": httpx.Response(200, json=self._packument(
                versions=["4.21.2", "5.0.0-beta.3"],
                latest="4.21.2",
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("express", "^4.21.2", Ecosystem.NODEJS, "package.json")
            result = await query_npm(client, dep, opts)

        assert result is None  # 5.0.0-beta.3 excluded; 4.21.2 is already current

    async def test_normal_mode_includes_prerelease_when_flag_set(self) -> None:
        from migratowl.registry import query_npm

        transport = _mock_transport({
            "/express": httpx.Response(200, json=self._packument(
                versions=["4.21.2", "5.0.0-beta.3"],
                latest="4.21.2",
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=True)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("express", "^4.21.2", Ecosystem.NODEJS, "package.json")
            result = await query_npm(client, dep, opts)

        assert result is not None
        from packaging.version import Version
        assert Version(result.latest_version) > Version("4.21.2")


# ===========================================================================
# Mode-aware query_pypi
# ===========================================================================


class TestQueryPypiModes:
    def _pypi_response(self, stable_version: str, all_versions: list[str]) -> dict:
        return {
            "info": {
                "version": stable_version,
                "home_page": None,
                "project_urls": None,
            },
            "releases": {v: [] for v in all_versions},
        }

    async def test_safe_mode_respects_ge_constraint(self) -> None:
        """>=2.28 in safe mode with 3.0.0 available → outdated (no upper bound)."""
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json=self._pypi_response(
                stable_version="3.0.0",
                all_versions=["2.28.0", "2.31.0", "3.0.0"],
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.SAFE, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("requests", ">=2.28.0", Ecosystem.PYTHON)
            result = await query_pypi(client, dep, opts)

        assert result is not None
        assert result.latest_version == "3.0.0"

    async def test_safe_mode_not_outdated_when_already_max_in_range(self) -> None:
        """~=2.31.0 (compatible release) with 2.31.2 as max patch → outdated if 2.31.2 exists."""
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json=self._pypi_response(
                stable_version="3.0.0",
                all_versions=["2.31.0", "2.31.1", "3.0.0"],
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.SAFE, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("requests", "~=2.31.0", Ecosystem.PYTHON)
            result = await query_pypi(client, dep, opts)

        # ~=2.31.0 means >=2.31.0,<2.32 — max in range is 2.31.1 → outdated
        assert result is not None
        assert result.latest_version == "2.31.1"

    async def test_normal_mode_uses_global_max(self) -> None:
        """Normal mode: ignore constraint, use max from all releases."""
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json=self._pypi_response(
                stable_version="3.0.0",
                all_versions=["2.28.0", "2.31.0", "3.0.0"],
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("requests", "~=2.28.0", Ecosystem.PYTHON)
            result = await query_pypi(client, dep, opts)

        assert result is not None
        assert result.latest_version == "3.0.0"

    async def test_prerelease_excluded_in_normal_mode_by_default(self) -> None:
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json=self._pypi_response(
                stable_version="2.31.0",
                all_versions=["2.31.0", "3.0.0a1"],
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("requests", "2.31.0", Ecosystem.PYTHON)
            result = await query_pypi(client, dep, opts)

        assert result is None  # 3.0.0a1 excluded, 2.31.0 is current

    async def test_prerelease_included_when_flag_set(self) -> None:
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json=self._pypi_response(
                stable_version="2.31.0",
                all_versions=["2.31.0", "3.0.0a1"],
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=True)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("requests", "2.31.0", Ecosystem.PYTHON)
            result = await query_pypi(client, dep, opts)

        assert result is not None
        assert result.latest_version == "3.0.0a1"


# ===========================================================================
# Mode-aware query_crates
# ===========================================================================


class TestQueryCratesModes:
    def _crates_response(self, newest: str, all_versions: list[str]) -> dict:
        return {
            "crate": {
                "newest_version": newest,
                "homepage": None,
                "repository": None,
                "documentation": None,
            },
            "versions": [{"num": v, "yanked": False} for v in all_versions],
        }

    async def test_safe_mode_caret_does_not_flag_major_bump(self) -> None:
        from migratowl.registry import query_crates

        transport = _mock_transport({
            "/api/v1/crates/serde": httpx.Response(200, json=self._crates_response(
                newest="2.0.0",
                all_versions=["1.0.195", "1.0.196", "2.0.0"],
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.SAFE, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("serde", "^1.0.195", Ecosystem.RUST, "Cargo.toml")
            result = await query_crates(client, dep, opts)

        assert result is not None
        assert result.latest_version == "1.0.196"

    async def test_normal_mode_flags_major_bump(self) -> None:
        from migratowl.registry import query_crates

        transport = _mock_transport({
            "/api/v1/crates/serde": httpx.Response(200, json=self._crates_response(
                newest="2.0.0",
                all_versions=["1.0.195", "1.0.196", "2.0.0"],
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("serde", "^1.0.195", Ecosystem.RUST, "Cargo.toml")
            result = await query_crates(client, dep, opts)

        assert result is not None
        assert result.latest_version == "2.0.0"


# ===========================================================================
# Mode-aware query_golang
# ===========================================================================


class TestQueryGolangModes:
    async def test_safe_mode_uses_latest_endpoint(self) -> None:
        """Go uses exact versions; safe and normal both use /@v/list for consistency."""
        from migratowl.registry import query_golang

        transport = _mock_transport({
            "/github.com/gin-gonic/gin/@v/list": httpx.Response(200, text="v1.9.0\nv1.10.0\n"),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.SAFE, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("github.com/gin-gonic/gin", "v1.9.0", Ecosystem.GO, "go.mod")
            result = await query_golang(client, dep, opts)

        assert result is not None
        assert result.latest_version == "v1.10.0"

    async def test_normal_mode_flags_newer_version(self) -> None:
        from migratowl.registry import query_golang

        transport = _mock_transport({
            "/github.com/gin-gonic/gin/@v/list": httpx.Response(200, text="v1.9.0\nv1.10.0\nv2.0.0\n"),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            dep = _dep("github.com/gin-gonic/gin", "v1.9.0", Ecosystem.GO, "go.mod")
            result = await query_golang(client, dep, opts)

        assert result is not None
        assert result.latest_version == "v2.0.0"


# ===========================================================================
# Mode-aware query_maven_central
# ===========================================================================


class TestQueryMavenCentralModes:
    def _maven_gav_response(self, versions: list[str]) -> dict:
        return {
            "response": {
                "docs": [{"v": v} for v in versions],
            },
        }

    async def test_normal_mode_uses_all_versions(self) -> None:
        from migratowl.registry import query_maven_central

        transport = _mock_transport({
            "/solrsearch/select": httpx.Response(200, json=self._maven_gav_response(
                ["3.2.0", "3.3.0", "3.3.1"],
            )),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://search.maven.org",
        ) as client:
            dep = _dep("org.springframework.boot:spring-boot-starter", "3.2.0", Ecosystem.JAVA, "pom.xml")
            result = await query_maven_central(client, dep, opts)

        assert result is not None
        assert result.latest_version == "3.3.1"


# ===========================================================================
# check_outdated with CheckOptions
# ===========================================================================


class TestCheckOutdatedWithOptions:
    async def test_default_options_uses_normal_mode(self) -> None:
        """Calling check_outdated with no options defaults to normal mode."""
        from migratowl.registry import check_outdated

        transport = _mock_transport({
            "/express": httpx.Response(200, json={
                "dist-tags": {"latest": "4.21.2"},
                "homepage": None,
                "repository": None,
                "versions": {"4.21.2": {}, "5.0.0": {}},
            }),
        })
        deps = [_dep("express", "^4.21.2", Ecosystem.NODEJS, "package.json")]
        async with httpx.AsyncClient(transport=transport) as client:
            result = await check_outdated(deps, concurrency=1, client=client)

        assert len(result) == 1  # normal mode: 5.0.0 exists → outdated
        assert result[0].latest_version == "5.0.0"

    async def test_normal_options_flags_major_bump(self) -> None:
        from migratowl.registry import check_outdated

        transport = _mock_transport({
            "/express": httpx.Response(200, json={
                "dist-tags": {"latest": "4.21.2"},
                "homepage": None,
                "repository": None,
                "versions": {"4.21.2": {}, "5.0.0": {}},
            }),
        })
        deps = [_dep("express", "^4.21.2", Ecosystem.NODEJS, "package.json")]
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await check_outdated(deps, options=opts, concurrency=1, client=client)

        assert len(result) == 1
        assert result[0].latest_version == "5.0.0"
