# SPDX-License-Identifier: Apache-2.0

"""Tests for changelog fetching and chunking logic."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from migratowl.changelog import (
    _extract_changelog_link,
    chunk_changelog_by_version,
    extract_breaking_changes,
    fetch_changelog,
    filter_chunks_by_version_range,
    truncate_chunks,
)


class TestChunkChangelogByVersion:
    def test_atx_headings(self) -> None:
        text = "## 2.0.0\nNew feature\n## 1.0.0\nInitial release\n"
        chunks = chunk_changelog_by_version(text)
        assert len(chunks) == 2
        assert chunks[0]["version"] == "2.0.0"
        assert "New feature" in chunks[0]["content"]
        assert chunks[1]["version"] == "1.0.0"

    def test_bold_headers(self) -> None:
        text = "**2.0.0** - 2024-01-01\nSome changes\n**1.0.0** - 2023-01-01\nFirst\n"
        chunks = chunk_changelog_by_version(text)
        assert len(chunks) == 2
        assert chunks[0]["version"] == "2.0.0"

    def test_rst_setext_headers(self) -> None:
        text = "2.0.0\n-----\nNew stuff\n1.0.0\n-----\nOld stuff\n"
        chunks = chunk_changelog_by_version(text)
        assert len(chunks) == 2
        assert chunks[0]["version"] == "2.0.0"
        assert chunks[1]["version"] == "1.0.0"

    def test_bracket_versions(self) -> None:
        text = "## [3.0.0]\nBreaking changes\n## [2.0.0]\nFeatures\n"
        chunks = chunk_changelog_by_version(text)
        assert len(chunks) == 2
        assert chunks[0]["version"] == "3.0.0"

    def test_empty_input(self) -> None:
        assert chunk_changelog_by_version("") == []
        assert chunk_changelog_by_version("   ") == []

    def test_no_version_headers(self) -> None:
        assert chunk_changelog_by_version("Just some text\nwithout versions\n") == []


class TestFilterChunksByVersionRange:
    def test_filters_correctly(self) -> None:
        chunks = [
            {"version": "3.0.0", "content": "v3"},
            {"version": "2.0.0", "content": "v2"},
            {"version": "1.5.0", "content": "v1.5"},
            {"version": "1.0.0", "content": "v1"},
        ]
        result = filter_chunks_by_version_range(chunks, "1.0.0", "2.0.0")
        assert len(result) == 2
        versions = [c["version"] for c in result]
        assert "1.5.0" in versions
        assert "2.0.0" in versions
        assert "1.0.0" not in versions
        assert "3.0.0" not in versions

    def test_empty_chunks(self) -> None:
        assert filter_chunks_by_version_range([], "1.0.0", "2.0.0") == []

    def test_invalid_versions_returns_all(self) -> None:
        chunks = [{"version": "abc", "content": "stuff"}]
        # Invalid range versions → fallback returns all chunks
        result = filter_chunks_by_version_range(chunks, "not.a.version", "also.not")
        assert result == chunks

    def test_mixed_version_and_tuple_comparison_does_not_crash(self) -> None:
        """Valid current/latest (parsed as Version) alongside a chunk whose
        version parses only via the numeric-tuple fallback must not raise
        TypeError from mixing Version and tuple. Found by fuzzing.

        "1.-1" fails packaging.Version but splits to the int tuple (1, -1),
        so the chunk takes the fallback path while current/latest are Versions.
        """
        chunks = [
            {"version": "1.2.3", "content": "valid semver"},
            {"version": "1.-1", "content": "tuple-only fallback"},
        ]
        # Must not raise; returns a list.
        result = filter_chunks_by_version_range(chunks, "1.0.0", "3.0.0")
        assert isinstance(result, list)




class TestExtractChangelogLink:
    def test_markdown_link_with_keyword(self) -> None:
        text = "See [CHANGELOG](https://example.com/changelog) for details."
        assert _extract_changelog_link(text) == "https://example.com/changelog"

    def test_url_with_keyword(self) -> None:
        text = "Check [here](https://example.com/CHANGES.md) for changes."
        assert _extract_changelog_link(text) == "https://example.com/CHANGES.md"

    def test_heading_with_bare_url(self) -> None:
        text = "## Changelog\nhttps://example.com/releases\n"
        assert _extract_changelog_link(text) == "https://example.com/releases"

    def test_returns_none_for_no_match(self) -> None:
        assert _extract_changelog_link("Just some regular text") is None

    def test_returns_none_for_empty(self) -> None:
        assert _extract_changelog_link("") is None


class TestFetchChangelog:
    async def test_direct_url_success(self) -> None:
        mock_client = AsyncMock()
        req = httpx.Request("GET", "https://example.com/CHANGELOG.md")
        mock_client.get.return_value = httpx.Response(
            200, text="## 2.0.0\nNew\n## 1.0.0\nOld\n", request=req
        )
        with patch("migratowl.changelog.get_http_client", return_value=mock_client):
            text, warnings = await fetch_changelog(
                "https://example.com/CHANGELOG.md", None, "testpkg"
            )
        assert "2.0.0" in text
        assert warnings == []

    async def test_github_raw_fallback(self) -> None:
        mock_client = AsyncMock()
        req = httpx.Request("GET", "https://example.com")
        # Direct URL fails
        mock_client.get.side_effect = [
            httpx.Response(404, request=req),
            # README fetch fails
            httpx.Response(404, request=req),
            httpx.Response(404, request=req),
            httpx.Response(404, request=req),
            httpx.Response(404, request=req),
            # GitHub raw probe succeeds
            httpx.Response(200, text="## 2.0.0\nChanges\n## 1.0.0\nInit\n", request=req),
        ]
        with (
            patch("migratowl.changelog.get_http_client", return_value=mock_client),
            patch("migratowl.changelog.get_settings") as mock_settings,
        ):
            mock_settings.return_value.github_token = ""
            text, warnings = await fetch_changelog(
                "https://example.com/CHANGELOG.md",
                "https://github.com/owner/repo",
                "testpkg",
            )
        assert "2.0.0" in text
        assert warnings == []

    async def test_no_urls_returns_warning(self) -> None:
        text, warnings = await fetch_changelog(None, None, "testpkg")
        assert text == ""
        assert len(warnings) == 1
        assert "testpkg" in warnings[0]

    async def test_all_strategies_fail_returns_warning(self) -> None:
        mock_client = AsyncMock()
        req = httpx.Request("GET", "https://example.com")
        mock_client.get.return_value = httpx.Response(404, request=req)

        with (
            patch("migratowl.changelog.get_http_client", return_value=mock_client),
            patch("migratowl.changelog.get_settings") as mock_settings,
        ):
            mock_settings.return_value.github_token = ""
            text, warnings = await fetch_changelog(
                None, "https://github.com/owner/repo", "testpkg"
            )
        assert text == ""
        assert len(warnings) == 1
        assert "testpkg" in warnings[0]


class TestExtractBreakingChanges:
    def test_chunks_with_breaking_changes_preserved(self) -> None:
        chunks = [
            {"version": "2.0.0", "content": "## BREAKING CHANGES\n- Removed old API\n\nOther stuff"},
            {"version": "1.1.0", "content": "## Deprecated\n- Old method deprecated"},
        ]
        result = extract_breaking_changes(chunks)
        assert len(result) == 2
        assert "Removed old API" in result[0]["content"]
        assert "deprecated" in result[1]["content"].lower()

    def test_chunks_without_breaking_changes(self) -> None:
        chunks = [
            {"version": "1.1.0", "content": "- Added new feature\n- Fixed typo"},
        ]
        result = extract_breaking_changes(chunks)
        assert len(result) == 1
        assert result[0]["version"] == "1.1.0"
        assert result[0]["content"] == "(no breaking changes noted)"

    def test_mixed_chunks(self) -> None:
        chunks = [
            {"version": "3.0.0", "content": "## Breaking Change\n- API removed\n\n## Features\n- New widget"},
            {"version": "2.1.0", "content": "- Minor improvements"},
        ]
        result = extract_breaking_changes(chunks)
        assert "API removed" in result[0]["content"]
        # Non-breaking content should not be in the extracted result
        assert result[1]["content"] == "(no breaking changes noted)"

    def test_empty_chunks(self) -> None:
        assert extract_breaking_changes([]) == []

    def test_case_insensitivity(self) -> None:
        for label in ["BREAKING CHANGE", "breaking change", "Breaking Change"]:
            chunks = [{"version": "1.0.0", "content": f"## {label}\n- Something changed"}]
            result = extract_breaking_changes(chunks)
            assert "Something changed" in result[0]["content"], f"Failed for label: {label}"

    def test_version_preserved(self) -> None:
        chunks = [{"version": "5.0.0", "content": "Migration guide: do X"}]
        result = extract_breaking_changes(chunks)
        assert result[0]["version"] == "5.0.0"

    def test_removal_keyword(self) -> None:
        chunks = [{"version": "2.0.0", "content": "- Removed legacy endpoint"}]
        result = extract_breaking_changes(chunks)
        assert "Removed legacy endpoint" in result[0]["content"]

    def test_migration_keyword(self) -> None:
        chunks = [{"version": "2.0.0", "content": "## Migration Guide\nDo X then Y"}]
        result = extract_breaking_changes(chunks)
        assert "Do X then Y" in result[0]["content"]

    def test_renamed_keyword(self) -> None:
        chunks = [{"version": "2.0.0", "content": "- Renamed `foo` to `bar`"}]
        result = extract_breaking_changes(chunks)
        assert "Renamed" in result[0]["content"]

    def test_no_longer_supported_keyword(self) -> None:
        chunks = [{"version": "2.0.0", "content": "- No longer supports Node 12"}]
        result = extract_breaking_changes(chunks)
        assert "No longer supports" in result[0]["content"]


class TestTruncateChunks:
    def test_under_budget(self) -> None:
        chunks = [
            {"version": "2.0.0", "content": "Small change"},
            {"version": "1.0.0", "content": "Initial"},
        ]
        result, truncated = truncate_chunks(chunks, 10_000)
        assert result == chunks
        assert truncated is False

    def test_over_budget_drops_oldest(self) -> None:
        chunks = [
            {"version": "3.0.0", "content": "A" * 100},
            {"version": "2.0.0", "content": "B" * 100},
            {"version": "1.0.0", "content": "C" * 100},
        ]
        # Each chunk is ~130 bytes in JSON; budget of 150 fits only the first
        result, truncated = truncate_chunks(chunks, 150)
        assert truncated is True
        assert len(result) < len(chunks)
        assert result[0]["version"] == "3.0.0"

    def test_single_large_chunk_truncated(self) -> None:
        chunks = [{"version": "1.0.0", "content": "X" * 10_000}]
        result, truncated = truncate_chunks(chunks, 500)
        assert truncated is True
        assert len(result) == 1
        assert result[0]["content"].endswith("... [truncated]")
        assert result[0]["version"] == "1.0.0"

    def test_empty_list(self) -> None:
        result, truncated = truncate_chunks([], 1000)
        assert result == []
        assert truncated is False

class TestRepositoryPageAsChangelogUrl:
    """A 'changelog' link that is only the GitHub repository page (e.g. aiofiles'
    https://github.com/Tinche/aiofiles#history) must not be fetched as HTML: the
    page parses to bogus versions and hides the repo's real changelog file."""

    async def test_repo_page_link_goes_to_repository_strategies(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        fetch_url = _AsyncMock(return_value="## 3.3\nfrom the HTML page")
        from_github = _AsyncMock(return_value="## 25.1.0\nreal changelog file")
        with (
            patch("migratowl.changelog._fetch_from_url", fetch_url),
            patch("migratowl.changelog._fetch_changelog_link_from_readme", _AsyncMock(return_value=None)),
            patch("migratowl.changelog._fetch_from_github", from_github),
            patch("migratowl.changelog._fetch_from_github_releases", _AsyncMock(side_effect=FileNotFoundError)),
            patch("migratowl.changelog.get_settings") as mock_settings,
        ):
            mock_settings.return_value.github_token = ""
            text, warnings = await fetch_changelog("https://github.com/Tinche/aiofiles#history", None, "aiofiles")

        assert text == "## 25.1.0\nreal changelog file"
        fetch_url.assert_not_awaited()
        from_github.assert_awaited_once_with("https://github.com/Tinche/aiofiles", None, None)

    async def test_file_links_on_github_are_still_fetched(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        fetch_url = _AsyncMock(return_value="## 2.0.0\nNew")
        with patch("migratowl.changelog._fetch_from_url", fetch_url):
            text, _ = await fetch_changelog("https://github.com/o/r/blob/main/CHANGES.rst", None, "pkg")
        assert text == "## 2.0.0\nNew"
        fetch_url.assert_awaited_once()


class TestStubFilePointingOffGithub:
    async def test_follows_moved_to_url_in_stub_file(self) -> None:
        # psutil's HISTORY.rst only says "History has moved to: https://psutil.io/changelog/".
        from unittest.mock import AsyncMock as _AsyncMock

        from migratowl.changelog import _fetch_from_github

        stub = "History has moved to:\n\n- https://psutil.io/changelog/\n"

        async def get(url: str, *args, **kwargs) -> httpx.Response:
            req = httpx.Request("GET", url)
            if url.endswith("/HEAD/HISTORY.rst"):
                return httpx.Response(200, text=stub, request=req)
            return httpx.Response(404, request=req)

        client = _AsyncMock()
        client.get.side_effect = get
        followed = _AsyncMock(return_value="8.0.0\n=====\nBig changes\n")
        with (
            patch("migratowl.changelog.get_http_client", return_value=client),
            patch("migratowl.changelog._fetch_from_url", followed),
        ):
            text = await _fetch_from_github("https://github.com/giampaolo/psutil")

        assert text == "8.0.0\n=====\nBig changes\n"
        followed.assert_awaited_once_with("https://psutil.io/changelog/")


class TestRangeAndExtractionFormats:
    def test_range_bounds_may_be_constraints(self) -> None:
        from migratowl.changelog import filter_chunks_by_version_range

        chunks = [{"version": v, "content": "x"} for v in ["8.0.0", "7.0.0", "6.0.0", "5.9.1", "5.9", "5.0.0"]]
        kept = filter_chunks_by_version_range(chunks, ">=5.9", "7.2.2")
        assert [c["version"] for c in kept] == ["7.0.0", "6.0.0", "5.9.1"]

    def test_caret_bound(self) -> None:
        from migratowl.changelog import filter_chunks_by_version_range

        chunks = [{"version": v, "content": "x"} for v in ["5.0.0", "4.21.2", "4.22.0"]]
        assert [c["version"] for c in filter_chunks_by_version_range(chunks, "^4.21.2", "5.0.0")] == ["5.0.0", "4.22.0"]

    def test_breaking_label_items_are_extracted(self) -> None:
        from migratowl.changelog import extract_breaking_changes

        out = extract_breaking_changes([{"version": "7.0.0",
                                         "content": "* fix: tidy\n* breaking: `memory_info()` tuple changed\n"}])
        assert "memory_info()" in out[0]["content"]

    def test_bold_headings_are_extracted(self) -> None:
        from migratowl.changelog import extract_breaking_changes

        out = extract_breaking_changes([{"version": "7.0.0",
                                         "content": "**Backward incompatible changes**\n\n- dropped Python 2.7\n"}])
        assert "Backward incompatible" in out[0]["content"]


class TestIndentedPrefixedItems:
    def test_indented_items_with_issue_and_platform_prefixes(self) -> None:
        from migratowl.changelog import extract_breaking_changes

        content = ("**New APIs**\n\n  * #669, [Windows]: `net_if_addrs()` also returns broadcast.\n\n"
                   "**API changes**\n\n  * #2490, breaking: remove long deprecated `Process.memory_info_ex()`.\n")
        out = extract_breaking_changes([{"version": "7.0.0", "content": content}])
        assert "memory_info_ex" in out[0]["content"]
        assert "broadcast" not in out[0]["content"]


class TestDefaultBranch:
    """Repos name their default branch anything; raw.githubusercontent.com resolves ``HEAD`` to it."""

    async def test_changelog_is_found_on_a_branch_called_develop(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        from migratowl.changelog import _fetch_from_github

        requested: list[str] = []

        async def get(url: str, *args, **kwargs) -> httpx.Response:
            requested.append(url)
            req = httpx.Request("GET", url)
            if url == "https://raw.githubusercontent.com/o/r/HEAD/CHANGELOG.md":
                return httpx.Response(200, text="## 2.0.0\n- Removed x\n\n## 1.0.0\n- First\n", request=req)
            return httpx.Response(404, request=req)

        client = _AsyncMock()
        client.get.side_effect = get
        with patch("migratowl.changelog.get_http_client", return_value=client):
            text = await _fetch_from_github("https://github.com/o/r")

        assert "Removed x" in text
        assert not any("/main/" in u or "/master/" in u for u in requested), "no per-branch guessing"


class TestReleasePagination:
    @staticmethod
    def _client(pages: dict[str, tuple[list[str], str | None]]) -> tuple[object, list[str]]:
        """Fake GitHub API: url -> (release tags, next url)."""
        from unittest.mock import AsyncMock as _AsyncMock

        requested: list[str] = []

        async def get(url: str, *args, **kwargs) -> httpx.Response:
            requested.append(url)
            tags, nxt = pages[url]
            headers = {"Link": f'<{nxt}>; rel="next"'} if nxt else {}
            body = [{"tag_name": t, "body": f"notes {t}", "draft": False, "prerelease": False} for t in tags]
            return httpx.Response(200, json=body, headers=headers, request=httpx.Request("GET", url))

        client = _AsyncMock()
        client.get.side_effect = get
        return client, requested

    BASE = "https://api.github.com/repos/o/r/releases?per_page=100"
    PAGES = {
        BASE: (["v4.0.0", "v3.0.0", "v2.1.0"], "https://p2"),
        "https://p2": (["v2.0.0", "v1.9.0"], "https://p3"),
        "https://p3": (["v1.0.0"], None),
    }

    async def test_stops_once_releases_at_or_below_the_current_version_are_reached(self) -> None:
        from migratowl.changelog import _fetch_from_github_releases

        client, requested = self._client(self.PAGES)
        with patch("migratowl.changelog.get_http_client", return_value=client):
            text = await _fetch_from_github_releases("https://github.com/o/r", current_version="2.0.0")

        assert requested == [self.BASE, "https://p2"], "page 3 only holds releases older than the current version"
        assert "## v4.0.0" in text and "## v2.0.0" in text

    async def test_without_a_current_version_every_page_is_read(self) -> None:
        from migratowl.changelog import _fetch_from_github_releases

        client, requested = self._client(self.PAGES)
        with patch("migratowl.changelog.get_http_client", return_value=client):
            await _fetch_from_github_releases("https://github.com/o/r")

        assert len(requested) == 3

    async def test_odd_tags_do_not_stop_pagination_early(self) -> None:
        from migratowl.changelog import _fetch_from_github_releases

        pages = {
            self.BASE: (["nightly", "pkg@3.0.0", "release-2.5"], "https://p2"),
            "https://p2": (["1.0.0"], None),
        }
        client, requested = self._client(pages)
        with patch("migratowl.changelog.get_http_client", return_value=client):
            await _fetch_from_github_releases("https://github.com/o/r", current_version="2.0.0")

        assert len(requested) == 2  # 1.0.0 is on page 2, so page 2 was needed to find it

    async def test_fetch_changelog_passes_the_current_version_down(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        releases = _AsyncMock(return_value="## 2.0.0\nnotes")
        with (
            patch("migratowl.changelog._fetch_changelog_link_from_readme", _AsyncMock(return_value=None)),
            patch("migratowl.changelog._fetch_from_github", _AsyncMock(side_effect=FileNotFoundError)),
            patch("migratowl.changelog._fetch_from_github_releases", releases),
            patch("migratowl.changelog.get_settings") as mock_settings,
        ):
            mock_settings.return_value.github_token = ""
            await fetch_changelog(None, "https://github.com/o/r", "pkg", current_version="1.5.0")

        releases.assert_awaited_once_with("https://github.com/o/r", "1.5.0")


class TestLinkedVersionHeaders:
    """release-please and Keep a Changelog tooling write ``## [1.2.3](compare-url) (date)``."""

    def test_version_headers_with_a_compare_link(self) -> None:
        from migratowl.changelog import _parse_version_from_line

        cases = {
            "## [v13.35.0](https://github.com/laravel/framework/compare/v13.34.0...v13.35.0) - 2026-10-06": "13.35.0",
            "## [1.2.3](https://github.com/o/r/compare/v1.2.2...v1.2.3) (2024-01-01)": "1.2.3",
            "### [2.0.0](https://github.com/o/r/compare/v1.9.0...v2.0.0) (2023-05-04)": "2.0.0",
            "## [5.0.0](https://example.com/r) - 2020-01-01": "5.0.0",
        }
        for line, expected in cases.items():
            assert _parse_version_from_line(line) == expected, line

    def test_prose_with_a_link_is_still_not_a_header(self) -> None:
        from migratowl.changelog import _parse_version_from_line

        assert _parse_version_from_line("1.2.3 fixed [the bug](https://x.io/y) in the parser today") is None

    def test_chunking_a_release_please_changelog(self) -> None:
        from migratowl.changelog import chunk_changelog_by_version

        text = (
            "# Changelog\n\n"
            "## [2.0.0](https://github.com/o/r/compare/v1.0.0...v2.0.0) (2024-02-01)\n\n"
            "### ⚠ BREAKING CHANGES\n\n* drop node 16\n\n"
            "## [1.0.0](https://github.com/o/r/compare/v0.9.0...v1.0.0) (2024-01-01)\n\n* first\n"
        )
        assert [c["version"] for c in chunk_changelog_by_version(text)] == ["2.0.0", "1.0.0"]


class TestExcerptPrefersEvidence:
    """MO-41: with no 'Breaking changes' heading, the lines that matter must come before the PR list."""

    @staticmethod
    def _express_style() -> list[dict]:
        prs = "\n".join(
            f"* {title} by @someone in https://github.com/expressjs/express/pull/{5000 + i}"
            for i, title in enumerate(
                ["4.19.2 Staging", "remove duplicate location test for data uri", "docs: update Security.md",
                 "Cut down on duplicated CI runs", "deprecate res.json(status, obj) in tests", "Add a Threat Model"] * 12
            )
        )
        body = (
            "Express v5 is finally here.\n\n"
            "### Major Changes in v5\n\n"
            "- **Node.js version support**: Dropped support for Node.js versions before v18.\n"
            "- **Routing changes**: Updated to path-to-regexp@8.x, removing sub-expression regex patterns.\n"
            "- **Deprecated API methods removed**: Removed old, deprecated API method signatures from Express v3/v4.\n"
            "- **Promise support**: Middleware can now return rejected promises.\n\n"
            "### What's Changed\n\n" + prs + "\n"
        )
        return [{"version": "5.0.0", "content": body}]

    def test_human_written_removals_come_before_the_pull_request_list(self) -> None:
        excerpt = extract_breaking_changes(self._express_style())[0]["content"][:1500]

        assert "Dropped support for Node.js versions before v18" in excerpt
        assert "removing sub-expression regex patterns" in excerpt
        assert "Deprecated API methods removed" in excerpt

    def test_auto_generated_pr_lines_rank_below_prose(self) -> None:
        content = extract_breaking_changes(self._express_style())[0]["content"]

        first_pr = content.index("in https://github.com/expressjs/express/pull/")
        assert content.index("Deprecated API methods removed") < first_pr
        assert content.index("Dropped support for Node.js") < first_pr

    def test_nothing_is_lost_only_reordered(self) -> None:
        original = self._express_style()[0]["content"]
        content = extract_breaking_changes(self._express_style())[0]["content"]

        assert "Threat Model" in content or len(content) >= len(original) * 0.5  # PR lines still follow the prose

    def test_short_notes_keep_their_order(self) -> None:
        chunks = [{"version": "2.0.0", "content": "### Breaking Changes\n\n* drop node 16\n\n### Fixes\n\n* a fix\n"}]

        content = extract_breaking_changes(chunks)[0]["content"]

        assert content.startswith("### Breaking Changes")
        assert "drop node 16" in content


class TestGithubPagesAreNotScraped:
    """MO-62.1: GitHub's HTML pages (releases, latest) are routed to the API, never parsed."""

    async def test_releases_page_link_is_a_repository_hint(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        fetch_url = _AsyncMock(return_value="## 7.0.1\nLatest")
        releases = _AsyncMock(return_value="## v5.0.0\n- Removed client option")
        with (
            patch("migratowl.changelog._fetch_from_url", fetch_url),
            patch("migratowl.changelog._fetch_changelog_link_from_readme", _AsyncMock(return_value=None)),
            patch("migratowl.changelog._fetch_from_github", _AsyncMock(side_effect=FileNotFoundError)),
            patch("migratowl.changelog._fetch_from_github_releases", releases),
            patch("migratowl.changelog.get_settings") as mock_settings,
        ):
            mock_settings.return_value.github_token = ""
            text, _ = await fetch_changelog("https://github.com/mde/ejs/releases/latest", None, "ejs")

        fetch_url.assert_not_awaited()
        assert releases.await_args.args[0] == "https://github.com/mde/ejs"
        assert "Removed client option" in text

    async def test_readme_link_to_github_releases_is_not_fetched_as_a_page(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        fetch_url = _AsyncMock(return_value="## 7.0.1\nLatest")
        from_github = _AsyncMock(return_value="## 2.0.0\n- Removed x")
        with (
            patch("migratowl.changelog._fetch_from_url", fetch_url),
            patch(
                "migratowl.changelog._fetch_changelog_link_from_readme",
                _AsyncMock(return_value="https://github.com/mde/ejs/releases/latest"),
            ),
            patch("migratowl.changelog._fetch_from_github", from_github),
            patch("migratowl.changelog._fetch_from_github_releases", _AsyncMock(side_effect=FileNotFoundError)),
            patch("migratowl.changelog.get_settings") as mock_settings,
        ):
            mock_settings.return_value.github_token = ""
            text, _ = await fetch_changelog(None, "https://github.com/mde/ejs", "ejs")

        fetch_url.assert_not_awaited()
        assert "Removed x" in text

    async def test_github_html_is_rejected_by_the_url_fetcher(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        from migratowl.changelog import _fetch_from_url

        page = "<!DOCTYPE html><html><body>Skip to content Navigation Menu <h2>v7.0.1</h2> Latest</body></html>"
        client = _AsyncMock()
        client.get.return_value = httpx.Response(
            200, text=page, request=httpx.Request("GET", "https://github.com/mde/ejs/releases")
        )
        with patch("migratowl.changelog.get_http_client", return_value=client), pytest.raises(ValueError):
            await _fetch_from_url("https://github.com/mde/ejs/releases")

    async def test_readme_is_read_from_the_default_branch(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        from migratowl.changelog import _fetch_changelog_link_from_readme

        requested: list[str] = []

        async def get(url: str, *args, **kwargs) -> httpx.Response:
            requested.append(url)
            return httpx.Response(404, request=httpx.Request("GET", url))

        client = _AsyncMock()
        client.get.side_effect = get
        with patch("migratowl.changelog.get_http_client", return_value=client):
            await _fetch_changelog_link_from_readme("https://github.com/o/r")

        assert requested and all("/HEAD/" in u for u in requested)


class TestVersionOnlyReleaseBodies:
    """MO-62.2: 'Version 7.0.1' is not release notes."""

    @staticmethod
    def _client(bodies: dict[str, str]):
        from unittest.mock import AsyncMock as _AsyncMock

        async def get(url: str, *args, **kwargs) -> httpx.Response:
            body = [{"tag_name": t, "name": t, "body": b, "draft": False, "prerelease": False} for t, b in bodies.items()]
            return httpx.Response(200, json=body, request=httpx.Request("GET", url))

        client = _AsyncMock()
        client.get.side_effect = get
        return client

    async def test_only_version_restatements_means_no_release_notes(self) -> None:
        from migratowl.changelog import _fetch_from_github_releases

        client = self._client({"v7.0.1": "Version 7.0.1", "v6.0.1": "v6.0.1", "v5.0.2": "Release 5.0.2\r\n", "v4.0.1": ""})
        with patch("migratowl.changelog.get_http_client", return_value=client), pytest.raises(FileNotFoundError):
            await _fetch_from_github_releases("https://github.com/mde/ejs")

    async def test_real_notes_are_kept_and_restatements_dropped(self) -> None:
        from migratowl.changelog import _fetch_from_github_releases

        client = self._client({"v2.0.0": "## Breaking\n- Removed foo()", "v1.9.9": "Version 1.9.9"})
        with patch("migratowl.changelog.get_http_client", return_value=client):
            text = await _fetch_from_github_releases("https://github.com/o/r")

        assert "Removed foo()" in text
        assert "v1.9.9" not in text


class TestPerMajorReleaseNotesFiles:
    """MO-62.3: ejs keeps its notes in RELEASE_NOTES_v4.md / RELEASE_NOTES_v5.md."""

    async def test_per_major_files_in_the_bump_range_are_found(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        from migratowl.changelog import _fetch_from_github, chunk_changelog_by_version, filter_chunks_by_version_range

        files = {
            "RELEASE_NOTES_v4.md": "# EJS Version 4.0.1 Release Notes\n\n## Major Changes\n- Dual module support\n",
            "RELEASE_NOTES_v5.md": "# EJS Version 5.0.1 Release Notes\n\n### Deprecated Option Removed\n"
                                   "- **Removed `client` option**\n",
        }
        requested: list[str] = []

        async def get(url: str, *args, **kwargs) -> httpx.Response:
            requested.append(url)
            name = url.split("/HEAD/", 1)[-1]
            req = httpx.Request("GET", url)
            return httpx.Response(200, text=files[name], request=req) if name in files else httpx.Response(404, request=req)

        client = _AsyncMock()
        client.get.side_effect = get
        with patch("migratowl.changelog.get_http_client", return_value=client):
            text = await _fetch_from_github("https://github.com/mde/ejs", current_version="3.1.10", latest_version="7.0.1")

        chunks = filter_chunks_by_version_range(chunk_changelog_by_version(text), "3.1.10", "7.0.1")
        versions = {c["version"]: c["content"] for c in chunks}
        assert set(versions) == {"4.0.1", "5.0.1"}
        assert "Removed `client` option" in versions["5.0.1"]
        assert not any("RELEASE_NOTES_v3" in u or "RELEASE_NOTES_v8" in u for u in requested), "only majors in range"

    async def test_without_versions_no_per_major_probing_happens(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        from migratowl.changelog import _fetch_from_github

        requested: list[str] = []

        async def get(url: str, *args, **kwargs) -> httpx.Response:
            requested.append(url)
            return httpx.Response(404, request=httpx.Request("GET", url))

        client = _AsyncMock()
        client.get.side_effect = get
        with patch("migratowl.changelog.get_http_client", return_value=client), pytest.raises(FileNotFoundError):
            await _fetch_from_github("https://github.com/o/r")

        assert not any("RELEASE_NOTES_v" in u for u in requested)

    async def test_fetch_changelog_passes_both_versions_to_the_file_strategy(self) -> None:
        from unittest.mock import AsyncMock as _AsyncMock

        from_github = _AsyncMock(return_value="## 2.0.0\nnotes")
        with (
            patch("migratowl.changelog._fetch_changelog_link_from_readme", _AsyncMock(return_value=None)),
            patch("migratowl.changelog._fetch_from_github", from_github),
            patch("migratowl.changelog.get_settings") as mock_settings,
        ):
            mock_settings.return_value.github_token = ""
            await fetch_changelog(None, "https://github.com/o/r", "pkg", current_version="1.0.0", latest_version="2.0.0")

        from_github.assert_awaited_once_with("https://github.com/o/r", "1.0.0", "2.0.0")


class TestDuplicateVersions:
    """MO-62.4: one version, one chunk."""

    def test_chunks_with_the_same_version_merge(self) -> None:
        from migratowl.changelog import merge_duplicate_versions

        merged = merge_duplicate_versions([
            {"version": "7.0.1", "content": ""},
            {"version": "6.0.0", "content": "six"},
            {"version": "7.0.1", "content": "real notes"},
            {"version": "7.0.1", "content": "more"},
        ])

        assert [c["version"] for c in merged] == ["7.0.1", "6.0.0"]
        assert merged[0]["content"] == "real notes\n\nmore"


class TestHtmlMainContent:
    """MO-62.6: documentation pages are converted from their main element, without the navigation."""

    PAGE = (
        "<!DOCTYPE html><html><body><nav>Skip to content <a>v9.9.9</a> Docs menu</nav>"
        "<div class='sidebar'><h2>1.0.0 docs</h2></div>"
        "<{tag}><h2>v2.0.0 (2026-10-08)</h2><ul><li>Removed the old API</li></ul>"
        "<div><div><h2>v1.5.0</h2><p>Fixes</p></div></div></{close}>"
        "<footer>Copyright</footer></body></html>"
    )

    async def _fetch(self, page: str) -> str:
        from unittest.mock import AsyncMock as _AsyncMock

        from migratowl.changelog import _fetch_from_url

        client = _AsyncMock()
        client.get.return_value = httpx.Response(
            200, text=page, request=httpx.Request("GET", "https://docs.example.org/changelog/")
        )
        with patch("migratowl.changelog.get_http_client", return_value=client):
            return await _fetch_from_url("https://docs.example.org/changelog/")

    @pytest.mark.parametrize("tag,close", [("main", "main"), ("article", "article"), ("div role=\"main\"", "div")])
    async def test_only_the_main_element_is_converted(self, tag: str, close: str) -> None:
        text = await self._fetch(self.PAGE.format(tag=tag, close=close))

        assert "Removed the old API" in text and "v1.5.0" in text
        assert "Skip to content" not in text and "9.9.9" not in text and "Copyright" not in text
        assert [c["version"] for c in chunk_changelog_by_version(text)] == ["2.0.0", "1.5.0"]

    async def test_pages_without_a_main_element_are_converted_whole(self) -> None:
        text = await self._fetch("<html><body><h2>2.0.0</h2><p>Removed x</p></body></html>")

        assert "Removed x" in text
