# SPDX-License-Identifier: Apache-2.0

"""Tests for registry query functions."""


import httpx
import pytest

from migratowl.models.schemas import Dependency, Ecosystem, OutdatedCheckMode, RegistryFailure
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
            outdated, failures = await check_outdated(deps, concurrency=5, client=client)

        assert len(outdated) == 1
        assert outdated[0].name == "requests"

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
            outdated, failures = await check_outdated(deps, concurrency=5, client=client)

        assert len(outdated) == 1
        assert outdated[0].name == "flask"

    async def test_empty_input(self) -> None:
        from migratowl.registry import check_outdated

        async with httpx.AsyncClient(transport=_mock_transport({})) as client:
            outdated, failures = await check_outdated([], concurrency=5, client=client)

        assert outdated == []
        assert failures == []


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
    def _maven_metadata(self, versions: list[str]) -> str:
        inner = "".join(f"<version>{v}</version>" for v in versions)
        return f"<metadata><versioning><versions>{inner}</versions></versioning></metadata>"

    async def test_normal_mode_uses_all_versions(self) -> None:
        from migratowl.registry import query_maven_central

        transport = _mock_transport({
            "/maven2/org/springframework/boot/spring-boot-starter/maven-metadata.xml": httpx.Response(
                200, text=self._maven_metadata(["3.2.0", "3.3.0", "3.3.1"]),
            ),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://repo1.maven.org",
        ) as client:
            dep = _dep("org.springframework.boot:spring-boot-starter", "3.2.0", Ecosystem.JAVA, "pom.xml")
            result = await query_maven_central(client, dep, opts)

        assert result is not None
        assert result.latest_version == "3.3.1"

    async def test_version_key_is_passed_through(self) -> None:
        from migratowl.registry import query_maven_central

        transport = _mock_transport({
            "/maven2/org/springframework/spring-core/maven-metadata.xml": httpx.Response(
                200, text=self._maven_metadata(["6.1.0", "6.2.0"]),
            ),
        })
        opts = CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=False)
        async with httpx.AsyncClient(transport=transport, base_url="https://repo1.maven.org") as client:
            dep = _dep("org.springframework:spring-core", "6.1.0", Ecosystem.JAVA, "pom.xml")
            dep.version_key = "spring.version"
            result = await query_maven_central(client, dep, opts)

        assert result is not None
        assert result.version_key == "spring.version"


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
            outdated, _ = await check_outdated(deps, concurrency=1, client=client)

        assert len(outdated) == 1  # normal mode: 5.0.0 exists → outdated
        assert outdated[0].latest_version == "5.0.0"

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
            outdated, _ = await check_outdated(deps, options=opts, concurrency=1, client=client)

        assert len(outdated) == 1
        assert outdated[0].latest_version == "5.0.0"

# ===========================================================================
# check_outdated — failure propagation (Issue 1)
# ===========================================================================


class TestCheckOutdatedReturnsFailures:
    async def test_failed_query_yields_registry_failure(self) -> None:
        from migratowl.registry import check_outdated

        transport = _mock_transport({})  # requests → 404 → HTTPStatusError

        deps = [_dep("requests", "2.31.0", Ecosystem.PYTHON)]

        async with httpx.AsyncClient(transport=transport) as client:
            outdated, failures = await check_outdated(deps, concurrency=5, client=client)

        assert outdated == []
        assert len(failures) == 1
        assert failures[0].name == "requests"
        assert failures[0].ecosystem == Ecosystem.PYTHON

    async def test_successful_query_has_no_failure(self) -> None:
        from migratowl.registry import check_outdated

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json={
                "info": {"version": "2.32.0", "home_page": None, "project_urls": None},
                "releases": {"2.31.0": [], "2.32.0": []},
            }),
        })

        deps = [_dep("requests", "2.31.0", Ecosystem.PYTHON)]

        async with httpx.AsyncClient(transport=transport) as client:
            outdated, failures = await check_outdated(deps, concurrency=5, client=client)

        assert len(outdated) == 1
        assert failures == []

    async def test_mixed_queries_tracks_both_separately(self) -> None:
        from migratowl.registry import check_outdated

        transport = _mock_transport({
            "/pypi/flask/json": httpx.Response(200, json={
                "info": {"version": "3.1.0", "home_page": None, "project_urls": None},
                "releases": {"3.0.0": [], "3.1.0": []},
            }),
            # requests → 404 → failure
        })

        deps = [
            _dep("flask", "3.0.0", Ecosystem.PYTHON),
            _dep("requests", "2.31.0", Ecosystem.PYTHON),
        ]

        async with httpx.AsyncClient(transport=transport) as client:
            outdated, failures = await check_outdated(deps, concurrency=5, client=client)

        assert len(outdated) == 1
        assert outdated[0].name == "flask"
        assert len(failures) == 1
        assert failures[0].name == "requests"


# ===========================================================================
# Semver ecosystems keep the registry's own version strings
# ===========================================================================


class TestSemverVersionStrings:
    """npm, crates and Go versions are semver: ``-x`` is a prerelease, and the
    version string must reach ``npm install`` / ``cargo update`` unchanged."""

    @staticmethod
    async def _npm(versions: list[str], current: str, *, include_prerelease: bool = False):
        from migratowl.registry import query_npm

        transport = _mock_transport({
            "/pkg": httpx.Response(200, json={"versions": {v: {} for v in versions}}),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            return await query_npm(
                client,
                _dep("pkg", current, Ecosystem.NODEJS, "package.json"),
                CheckOptions(include_prerelease=include_prerelease),
            )

    async def test_npm_dash_zero_is_a_prerelease_not_the_latest(self) -> None:
        # rollup published 5.0.0-0; PEP 440 reads it as 5.0.0.post0 (a release).
        result = await self._npm(["3.30.0", "4.9.0", "5.0.0-0"], "3.30.0")
        assert result is not None
        assert result.latest_version == "4.9.0"

    async def test_npm_prerelease_keeps_its_npm_spelling(self) -> None:
        result = await self._npm(["1.0.0", "1.1.0-beta.1"], "1.0.0", include_prerelease=True)
        assert result is not None
        assert result.latest_version == "1.1.0-beta.1"

    async def test_npm_prerelease_sorts_before_its_release(self) -> None:
        result = await self._npm(["1.0.0", "2.0.0-rc.1", "2.0.0"], "1.0.0", include_prerelease=True)
        assert result.latest_version == "2.0.0"

    async def test_npm_numeric_prerelease_identifiers_compare_numerically(self) -> None:
        result = await self._npm(["1.0.0", "2.0.0-beta.2", "2.0.0-beta.10"], "1.0.0", include_prerelease=True)
        assert result.latest_version == "2.0.0-beta.10"

    async def test_crates_dash_zero_is_a_prerelease(self) -> None:
        from migratowl.registry import query_crates

        transport = _mock_transport({
            "/api/v1/crates/serde": httpx.Response(200, json={
                "crate": {},
                "versions": [{"num": v, "yanked": False} for v in ["1.0.0", "1.2.0", "2.0.0-0"]],
            }),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await query_crates(client, _dep("serde", "1.0.0", Ecosystem.RUST, "Cargo.toml"))
        assert result is not None
        assert result.latest_version == "1.2.0"

    async def test_go_rc_is_a_prerelease(self) -> None:
        from migratowl.registry import query_golang

        transport = _mock_transport({
            "/github.com/a/b/@v/list": httpx.Response(200, text="v1.0.0\nv1.5.0\nv1.6.0-rc.1\n"),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await query_golang(client, _dep("github.com/a/b", "1.0.0", Ecosystem.GO, "go.mod"))
        assert result is not None
        assert result.latest_version == "v1.5.0"

    async def test_pypi_keeps_the_published_spelling(self) -> None:
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/pkg/json": httpx.Response(200, json={"info": {}, "releases": {"1.0": [], "2.0-1": []}}),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await query_pypi(client, _dep("pkg", "==1.0", Ecosystem.PYTHON))
        assert result is not None
        assert result.latest_version == "2.0-1"  # not normalized to "2.0.post1"


class TestPypiYanked:
    @staticmethod
    async def _latest(releases: dict) -> str | None:
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json={"info": {}, "releases": releases}),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await query_pypi(client, _dep("requests", "==2.31.0", Ecosystem.PYTHON))
        return result.latest_version if result else None

    async def test_fully_yanked_release_is_never_latest(self) -> None:
        latest = await self._latest({
            "2.31.0": [{"yanked": False}],
            "2.32.0": [{"yanked": True}, {"yanked": True}],
            "2.32.1": [{"yanked": False}],
            "2.33.0": [{"yanked": True}],
        })
        assert latest == "2.32.1"

    async def test_release_with_one_unyanked_file_still_counts(self) -> None:
        latest = await self._latest({
            "2.31.0": [{"yanked": False}],
            "2.32.0": [{"yanked": True}, {"yanked": False}],
        })
        assert latest == "2.32.0"


class TestCheckOutdatedUsesSharedClient:
    async def test_registry_queries_are_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Registries throttle with 429/503; the shared client's RetryTransport retries them.
        from migratowl import registry
        from migratowl.http import RetryTransport

        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host != "registry.npmjs.org":
                return httpx.Response(404)  # the deps.dev repository lookup is not under test here
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={"versions": {"1.0.0": {}, "2.0.0": {}}})

        client = httpx.AsyncClient(transport=RetryTransport(httpx.MockTransport(handler), backoff_base=0.0))
        monkeypatch.setattr(registry, "get_http_client", lambda: client)
        try:
            outdated, failures = await registry.check_outdated([_dep("pkg", "1.0.0", Ecosystem.NODEJS, "package.json")])
        finally:
            await client.aclose()

        assert failures == []
        assert [o.latest_version for o in outdated] == ["2.0.0"]
        assert calls["n"] == 2


class TestPypiRequiresPython:
    """Releases the sandbox's Python cannot install are not upgrade targets."""

    @staticmethod
    async def _latest(releases: dict, python_version: str | None) -> str | None:
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/sphinx/json": httpx.Response(200, json={"info": {}, "releases": releases}),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await query_pypi(
                client, _dep("sphinx", "==7.4.7", Ecosystem.PYTHON), CheckOptions(python_version=python_version)
            )
        return result.latest_version if result else None

    RELEASES = {
        "7.4.7": [{"requires_python": ">=3.9"}],
        "9.0.0": [{"requires_python": ">=3.11"}],
        "9.1.0": [{"requires_python": ">=3.14"}],
        "9.2.0": [{"requires_python": "not a specifier"}],
    }

    async def test_skips_releases_requiring_a_newer_python(self) -> None:
        releases = {k: v for k, v in self.RELEASES.items() if k != "9.2.0"}
        assert await self._latest(releases, "3.13") == "9.0.0"

    async def test_without_python_version_every_release_counts(self) -> None:
        releases = {k: v for k, v in self.RELEASES.items() if k != "9.2.0"}
        assert await self._latest(releases, None) == "9.1.0"

    async def test_unparseable_requires_python_is_not_excluded(self) -> None:
        assert await self._latest(self.RELEASES, "3.13") == "9.2.0"

    async def test_missing_requires_python_is_not_excluded(self) -> None:
        assert await self._latest({"7.4.7": [{}], "8.0.0": [{"requires_python": None}]}, "3.13") == "8.0.0"


class TestInstalledVersionFromLockfile:
    async def test_range_already_at_latest_is_not_outdated(self) -> None:
        # Declared ^4.18.0 but the lockfile installs 4.21.2, which is the latest.
        from migratowl.registry import query_npm

        transport = _mock_transport({
            "/express": httpx.Response(200, json={"versions": {"4.18.0": {}, "4.21.2": {}}}),
        })
        dep = Dependency(name="express", current_version="^4.18.0", ecosystem=Ecosystem.NODEJS,
                         manifest_path="package.json", installed_version="4.21.2")
        async with httpx.AsyncClient(transport=transport) as client:
            assert await query_npm(client, dep) is None

    async def test_outdated_result_carries_installed_version(self) -> None:
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/requests/json": httpx.Response(200, json={"info": {}, "releases": {"2.31.0": [], "2.32.3": []}}),
        })
        dep = Dependency(name="requests", current_version=">=2.0", ecosystem=Ecosystem.PYTHON,
                         manifest_path="pyproject.toml", installed_version="2.31.0")
        async with httpx.AsyncClient(transport=transport) as client:
            result = await query_pypi(client, dep)
        assert result is not None
        assert (result.current_version, result.installed_version, result.latest_version) == (">=2.0", "2.31.0", "2.32.3")


class TestRepositoryUrlDiscovery:
    @staticmethod
    async def _pypi(info: dict) -> object:
        from migratowl.registry import query_pypi

        transport = _mock_transport({
            "/pypi/pkg/json": httpx.Response(200, json={"info": info, "releases": {"1.0": [], "2.0": []}}),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            return await query_pypi(client, _dep("pkg", "==1.0", Ecosystem.PYTHON))

    async def test_code_key_is_a_repository(self) -> None:
        # Sphinx labels its repository "Code".
        result = await self._pypi({"project_urls": {"Code": "https://github.com/sphinx-doc/sphinx",
                                                    "Homepage": "https://www.sphinx-doc.org/"}})
        assert result.repository_url == "https://github.com/sphinx-doc/sphinx"

    async def test_github_homepage_is_the_repository_fallback(self) -> None:
        # psutil only publishes a GitHub homepage.
        result = await self._pypi({"home_page": "https://github.com/giampaolo/psutil",
                                   "project_urls": {"Homepage": "https://github.com/giampaolo/psutil"}})
        assert result.repository_url == "https://github.com/giampaolo/psutil"

    async def test_non_forge_homepage_is_not_a_repository(self) -> None:
        result = await self._pypi({"project_urls": {"Homepage": "https://www.sphinx-doc.org/"}})
        assert result.repository_url is None

    async def test_npm_github_homepage_fallback(self) -> None:
        from migratowl.registry import query_npm

        transport = _mock_transport({
            "/pkg": httpx.Response(200, json={"homepage": "https://github.com/o/pkg#readme",
                                             "versions": {"1.0.0": {}, "2.0.0": {}}}),
        })
        async with httpx.AsyncClient(transport=transport) as client:
            result = await query_npm(client, _dep("pkg", "1.0.0", Ecosystem.NODEJS, "package.json"))
        assert result.repository_url == "https://github.com/o/pkg"


class TestGoMajorVersionModules:
    """Go majors >= 2 live at <module>/vN; the base path's version list never shows them."""

    @staticmethod
    async def _go(name: str, current: str, lists: dict[str, str], mode=None):
        from migratowl.registry import query_golang

        transport = _mock_transport({f"/{path}/@v/list": httpx.Response(200, text=body) for path, body in lists.items()})
        options = CheckOptions(mode=mode) if mode else CheckOptions()
        async with httpx.AsyncClient(transport=transport) as client:
            return await query_golang(client, _dep(name, current, Ecosystem.GO, "go.mod"), options)

    LISTS = {
        "github.com/x/y": "v1.0.0\nv1.5.0\n",
        "github.com/x/y/v2": "v2.0.0\nv2.3.0\n",
        "github.com/x/y/v3": "v3.0.0-rc.1\n",  # prerelease only: not a stable major
    }

    async def test_reports_newest_major_module(self) -> None:
        result = await self._go("github.com/x/y", "1.0.0", self.LISTS)
        assert (result.latest_version, result.module_path) == ("v2.3.0", "github.com/x/y/v2")

    async def test_module_already_on_v2_probes_v3(self) -> None:
        lists = {**self.LISTS, "github.com/x/y/v3": "v3.0.0\nv3.1.0\n"}
        result = await self._go("github.com/x/y/v2", "2.0.0", lists)
        assert (result.latest_version, result.module_path) == ("v3.1.0", "github.com/x/y/v3")

    async def test_no_newer_major_keeps_module_path_empty(self) -> None:
        result = await self._go("github.com/x/y", "1.0.0", {"github.com/x/y": "v1.0.0\nv1.5.0\n"})
        assert (result.latest_version, result.module_path) == ("v1.5.0", None)

    async def test_safe_mode_stays_on_the_declared_major(self) -> None:
        from migratowl.models.schemas import OutdatedCheckMode

        result = await self._go("github.com/x/y", "1.0.0", self.LISTS, mode=OutdatedCheckMode.SAFE)
        assert (result.latest_version, result.module_path) == ("v1.5.0", None)

    async def test_gopkg_in_is_not_probed(self) -> None:
        result = await self._go("gopkg.in/yaml.v2", "2.4.0", {"gopkg.in/yaml.v2": "v2.4.0\nv2.4.1\n"})
        assert (result.latest_version, result.module_path) == ("v2.4.1", None)


# ===========================================================================
# Mirrors and private registries
# ===========================================================================


class TestRegistryMirrors:
    """Version checks follow the configured mirror and send credentials to it only."""

    @staticmethod
    def _recording_client(handler) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
        seen: list[httpx.Request] = []

        def wrapped(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(wrapped)), seen

    @staticmethod
    def _options(**kwargs):
        from migratowl.registries import Registries

        return CheckOptions(mode=OutdatedCheckMode.NORMAL, registries=Registries(**kwargs))

    async def test_npm_uses_the_mirror_and_bearer_token(self) -> None:
        from migratowl.registry import query_npm

        client, seen = self._recording_client(lambda r: httpx.Response(200, json={"versions": {"1.0.0": {}, "2.0.0": {}}}))
        options = self._options(npm="https://npm.corp/api/npm", token="tok")
        async with client:
            result = await query_npm(client, _dep("left-pad", "1.0.0", Ecosystem.NODEJS, "package.json"), options)

        assert result is not None and result.latest_version == "2.0.0"
        assert str(seen[0].url) == "https://npm.corp/api/npm/left-pad"
        assert seen[0].headers["Authorization"] == "Bearer tok"

    async def test_pypi_uses_the_mirror_with_basic_auth(self) -> None:
        from migratowl.registry import query_pypi

        client, seen = self._recording_client(lambda r: httpx.Response(200, json={
            "info": {"version": "2.0"}, "releases": {"1.0": [{}], "2.0": [{}]}}))
        options = self._options(pypi="https://pypi.corp", username="svc", password="pw")
        async with client:
            await query_pypi(client, _dep("requests", "1.0", Ecosystem.PYTHON), options)

        assert str(seen[0].url) == "https://pypi.corp/pypi/requests/json"
        assert seen[0].headers["Authorization"].startswith("Basic ")

    async def test_public_registry_never_gets_the_credential(self) -> None:
        from migratowl.registry import query_npm

        client, seen = self._recording_client(lambda r: httpx.Response(200, json={"versions": {"1.0.0": {}}}))
        options = self._options(npm="https://npm.corp", pypi="https://pypi.corp", token="tok")
        # npm is a mirror, but a crates request (public host) must not carry the token
        from migratowl.registry import query_crates

        async with client:
            await query_npm(client, _dep("a", "1.0.0", Ecosystem.NODEJS, "package.json"), options)
            try:
                await query_crates(client, _dep("serde", "1.0.0", Ecosystem.RUST, "Cargo.toml"), options)
            except Exception:
                pass

        assert "Authorization" in seen[0].headers
        assert seen[1].url.host == "crates.io"
        assert "Authorization" not in seen[1].headers

    async def test_go_proxy_mirror(self) -> None:
        from migratowl.registry import query_golang

        client, seen = self._recording_client(lambda r: httpx.Response(200, text="v1.0.0\nv1.1.0\n"))
        options = self._options(go_proxy="https://go.corp/proxy")
        async with client:
            result = await query_golang(client, _dep("example.com/m", "v1.0.0", Ecosystem.GO, "go.mod"), options)

        assert result is not None and result.latest_version == "v1.1.0"
        assert all(r.url.host == "go.corp" for r in seen)
        assert str(seen[0].url).startswith("https://go.corp/proxy/example.com/m/@v/list")

    async def test_maven_mirror_reads_maven_metadata(self) -> None:
        from migratowl.registry import query_maven_central

        metadata = (
            "<metadata><groupId>org.x</groupId><artifactId>lib</artifactId><versioning>"
            "<versions><version>1.0</version><version>2.0</version><version>2.1-SNAPSHOT</version></versions>"
            "</versioning></metadata>"
        )
        client, seen = self._recording_client(lambda r: httpx.Response(200, text=metadata))
        options = self._options(maven="https://mvn.corp/repo", username="u", password="p")
        async with client:
            result = await query_maven_central(client, _dep("org.x:lib", "1.0", Ecosystem.JAVA, "pom.xml"), options)

        assert str(seen[0].url) == "https://mvn.corp/repo/org/x/lib/maven-metadata.xml"
        assert result is not None and result.latest_version == "2.0"  # the SNAPSHOT is a pre-release
        assert seen[0].headers["Authorization"].startswith("Basic ")

    async def test_defaults_are_unchanged(self) -> None:
        from migratowl.registry import query_npm

        client, seen = self._recording_client(lambda r: httpx.Response(200, json={"versions": {"1.0.0": {}}}))
        async with client:
            await query_npm(client, _dep("a", "1.0.0", Ecosystem.NODEJS, "package.json"))

        assert str(seen[0].url) == "https://registry.npmjs.org/a"
        assert "Authorization" not in seen[0].headers


class TestCleanGitUrl:
    """npm repository fields come in several git spellings; changelog lookup needs the https page."""

    def test_spellings_become_https(self) -> None:
        from migratowl.registry import _clean_git_url

        cases = {
            "git://github.com/mde/ejs.git": "https://github.com/mde/ejs",
            "git+https://github.com/expressjs/express.git": "https://github.com/expressjs/express",
            "git+ssh://git@github.com/o/r.git": "https://github.com/o/r",
            "ssh://git@gitlab.com/g/p.git": "https://gitlab.com/g/p",
            "git@github.com:o/r.git": "https://github.com/o/r",
            "github:o/r": "https://github.com/o/r",
            "https://github.com/o/r": "https://github.com/o/r",
        }
        for raw, expected in cases.items():
            assert _clean_git_url(raw) == expected, raw


class TestDepsDevRepositoryFallback:
    """MO-62.5: packages whose registry names no repository get one from deps.dev."""

    @staticmethod
    def _handler(seen: list[str], deps_dev_status: int = 200):
        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.url.host == "repo1.maven.org":
                return httpx.Response(200, text="<metadata><versioning><versions><version>32.0.0</version>"
                                                "<version>33.3.1</version></versions></versioning></metadata>")
            if request.url.host == "api.deps.dev":
                return httpx.Response(deps_dev_status, json={"relatedProjects": [
                    {"projectKey": {"id": "github.com/google/guava"}, "relationType": "ISSUE_TRACKER"},
                    {"projectKey": {"id": "github.com/google/guava"}, "relationType": "SOURCE_REPO"},
                ]})
            if request.url.host == "registry.npmjs.org":
                return httpx.Response(200, json={"versions": {"1.0.0": {}, "2.0.0": {}},
                                                 "repository": {"url": "git+https://github.com/o/r.git"}})
            return httpx.Response(404)
        return handle

    async def _check(self, deps, seen, options=None, deps_dev_status: int = 200):
        from migratowl.registry import check_outdated

        async with httpx.AsyncClient(transport=httpx.MockTransport(self._handler(seen, deps_dev_status))) as client:
            outdated, failures = await check_outdated(deps, options or CheckOptions(mode=OutdatedCheckMode.NORMAL),
                                                      client=client)
        assert failures == []
        return outdated

    async def test_maven_package_gets_its_repository_from_deps_dev(self) -> None:
        seen: list[str] = []
        outdated = await self._check([_dep("com.google.guava:guava", "32.0.0", Ecosystem.JAVA, "pom.xml")], seen)

        assert outdated[0].repository_url == "https://github.com/google/guava"
        assert any(u.startswith("https://api.deps.dev/v3/systems/maven/packages/com.google.guava%3Aguava/") for u in seen)

    async def test_a_known_repository_is_not_looked_up(self) -> None:
        seen: list[str] = []
        outdated = await self._check([_dep("left-pad", "1.0.0", Ecosystem.NODEJS, "package.json")], seen)

        assert outdated[0].repository_url == "https://github.com/o/r"
        assert not any("deps.dev" in u for u in seen)

    async def test_private_mirrors_never_send_package_names_to_deps_dev(self) -> None:
        from migratowl.registries import Registries

        seen: list[str] = []
        options = CheckOptions(mode=OutdatedCheckMode.NORMAL, registries=Registries(npm="https://npm.corp"))
        await self._check([_dep("com.google.guava:guava", "32.0.0", Ecosystem.JAVA, "pom.xml")], seen, options)

        assert not any("deps.dev" in u for u in seen)

    async def test_a_deps_dev_failure_leaves_the_repository_empty(self) -> None:
        seen: list[str] = []
        outdated = await self._check(
            [_dep("com.google.guava:guava", "32.0.0", Ecosystem.JAVA, "pom.xml")], seen, deps_dev_status=500
        )

        assert outdated[0].repository_url is None


class TestMavenVersionStrings:
    """Maven qualifiers (-jre, -android, .Final, -M1) are not PEP 440; they must still compare."""

    @staticmethod
    async def _latest(current: str, versions: list[str], include_prerelease: bool = False) -> str | None:
        from migratowl.registry import query_maven_central

        inner = "".join(f"<version>{v}</version>" for v in versions)
        xml = f"<metadata><versioning><versions>{inner}</versions></versioning></metadata>"

        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=xml))) as client:
            result = await query_maven_central(
                client, _dep("g:a", current, Ecosystem.JAVA, "pom.xml"),
                CheckOptions(mode=OutdatedCheckMode.NORMAL, include_prerelease=include_prerelease),
            )
        return result.latest_version if result else None

    async def test_guava_flavours_stay_apart(self) -> None:
        versions = ["31.0-jre", "31.0-android", "33.5.0-jre", "33.5.0-android", "34.0.0-rc1-jre"]
        assert await self._latest("31.0-jre", versions) == "33.5.0-jre"
        assert await self._latest("31.0-android", versions) == "33.5.0-android"

    async def test_final_and_ga_are_releases(self) -> None:
        versions = ["5.6.15.Final", "6.6.0.Final", "7.0.0.Beta1", "7.0.0.CR2"]
        assert await self._latest("5.6.15.Final", versions) == "6.6.0.Final"
        assert await self._latest("5.6.15.Final", versions, include_prerelease=True) == "7.0.0.CR2"

    async def test_milestones_are_prereleases(self) -> None:
        assert await self._latest("5.3.0", ["5.3.0", "6.0.0-M1", "5.3.39"]) == "5.3.39"

    async def test_snapshots_are_ignored(self) -> None:
        assert await self._latest("1.0.0", ["1.0.0", "2.0.0-SNAPSHOT"]) is None


class TestDepsDevLinks:
    async def test_source_repo_link_is_used_and_apache_gitbox_maps_to_github(self) -> None:
        from migratowl.models.schemas import OutdatedDependency
        from migratowl.registry import _deps_dev_repository

        body = {"links": [{"label": "SOURCE_REPO", "url": "https://gitbox.apache.org/repos/asf/commons-lang.git"}],
                "relatedProjects": [{"projectKey": {"id": ""}, "relationType": "ISSUE_TRACKER"}]}
        dep = OutdatedDependency(name="org.apache.commons:commons-lang3", current_version="3.12.0",
                                 latest_version="3.21.0", ecosystem=Ecosystem.JAVA, manifest_path="pom.xml")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as client:
            assert await _deps_dev_repository(client, dep) == "https://github.com/apache/commons-lang"
