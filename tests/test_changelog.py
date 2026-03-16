"""Tests for changelog fetching and chunking logic."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from migratowl.changelog import (
    _extract_changelog_link,
    chunk_changelog_by_version,
    fetch_changelog,
    filter_chunks_by_version_range,
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
