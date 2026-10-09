# SPDX-License-Identifier: Apache-2.0

"""Tests for changelog fetching and chunking logic."""

from unittest.mock import AsyncMock, patch

import httpx

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
        from_github.assert_awaited_once_with("https://github.com/Tinche/aiofiles")

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
            if url.endswith("/master/HISTORY.rst"):
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
