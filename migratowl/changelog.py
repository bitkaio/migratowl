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

"""Changelog fetching and chunking for dependency analysis."""

from __future__ import annotations

import asyncio
import json
import logging
import re

import html2text as _html2text
import httpx
from packaging.version import InvalidVersion, Version

from migratowl.config import get_settings
from migratowl.http import get_http_client

logger = logging.getLogger(__name__)


async def fetch_changelog(
    changelog_url: str | None,
    repository_url: str | None,
    dep_name: str,
) -> tuple[str, list[str]]:
    """Fetch changelog text, trying changelog_url first, then GitHub raw fallback.

    Returns (text, warnings) where warnings is a list of diagnostic messages
    explaining why the changelog could not be fetched (empty on success).
    """
    if not changelog_url and not repository_url:
        return "", [f"No changelog URL or repository URL provided for {dep_name}"]

    if changelog_url:
        try:
            return await _fetch_from_url(changelog_url), []
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError, FileNotFoundError) as exc:
            logger.debug("changelog_url fetch failed for %s: %s", dep_name, exc)

    # Step 2: extract changelog link from README.
    if repository_url:
        readme_link = await _fetch_changelog_link_from_readme(repository_url)
        if readme_link and readme_link != changelog_url:
            try:
                return await _fetch_from_url(readme_link), []
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError, FileNotFoundError) as exc:
                logger.debug("readme changelog link fetch failed for %s: %s", dep_name, exc)

    if repository_url:
        settings = get_settings()
        # With a token: API is cheap (5 000 req/hr) → try it before slow file probing.
        # Without token: preserve quota (60 req/hr) → file probing first, API last.
        ordered = (
            [_fetch_from_github_releases, _fetch_from_github]
            if settings.github_token
            else [_fetch_from_github, _fetch_from_github_releases]
        )
        for strategy in ordered:
            try:
                return await strategy(repository_url), []
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError, FileNotFoundError) as exc:
                logger.debug("strategy %s failed for %s: %s", strategy.__name__, dep_name, exc)

    return "", [f"Could not fetch changelog for {dep_name}"]


async def _fetch_from_url(url: str) -> str:
    """Fetch raw text from a URL with redirect following.

    If the response is HTML, strips it to plain text with html2text and checks
    for parseable version headers.  Raises ValueError if no version headers are
    found after stripping (triggers the GitHub raw-file fallback).
    """
    client = get_http_client()
    response = await client.get(url)
    response.raise_for_status()
    text = response.text
    if text.lstrip().startswith(("<", "<!DOCTYPE", "<!doctype")):
        converter = _html2text.HTML2Text()
        converter.ignore_links = True
        converter.ignore_images = True
        converter.body_width = 0
        stripped = converter.handle(text)
        if not chunk_changelog_by_version(stripped):
            raise ValueError(f"HTML response with no parseable version headers: {url}")
        return stripped
    return text


# Regex to find a GitHub blob URL embedded in stub/redirect files.
_GITHUB_BLOB_RE = re.compile(r"https?://github\.com/([^/\s]+)/([^/\s]+)/blob/([^/\s]+)/([^\s`>\"']+)")

# --- README changelog-link extraction regexes ---

_CHANGELOG_LINK_KEYWORDS_RE = re.compile(
    r"change[\s_-]?log|changes|history|releases|news|what.?s[\s_-]?new",
    re.IGNORECASE,
)

# Captures link text (group 1) and URL (group 2), supports one level of nesting for badges.
_MD_LINK_RE = re.compile(r"\[([^\[\]]*(?:\[[^\]]*\][^\[\]]*)*)\]\(([^)\s]+)\)")

_CHANGELOG_HEADING_RE = re.compile(
    r"^#{1,6}\s*(?:change[\s_-]?log|changes|history|releases|news|what.?s[\s_-]?new)",
    re.IGNORECASE | re.MULTILINE,
)

_BARE_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")

_README_CANDIDATES = [
    ("README.md", "main"),
    ("README.md", "master"),
    ("README.rst", "main"),
    ("README.rst", "master"),
]

_GITHUB_OWNER_REPO_RE = re.compile(r"github\.com[/:]([^/]+)/([^/#]+?)(?:\.git)?(?:[#/]|$)")


def _extract_changelog_link(text: str) -> str | None:
    """Scan raw README text for a changelog URL.

    Strategies (in order):
    1. Markdown links where text or URL contains changelog keywords.
    2. Badge-wrapped links ``[![...](badge)](url)`` where URL contains keywords.
    3. Heading (``## Changelog``) followed by a bare URL within 5 lines.
    """
    if not text:
        return None

    # Strategy 1 & 2: Markdown links (badge-wrapped links are captured by the
    # same regex since nested [] are supported).
    for m in _MD_LINK_RE.finditer(text):
        link_text, url = m.group(1), m.group(2)
        if not url.startswith(("http://", "https://")):
            continue
        if _CHANGELOG_LINK_KEYWORDS_RE.search(link_text) or _CHANGELOG_LINK_KEYWORDS_RE.search(url):
            return url

    # Strategy 3: Heading + bare URL within 5 lines.
    for heading_match in _CHANGELOG_HEADING_RE.finditer(text):
        after = text[heading_match.end() :]
        lines_after = after.split("\n", 6)[:6]  # heading line remainder + 5 lines
        for line in lines_after:
            url_match = _BARE_URL_RE.search(line)
            if url_match:
                return url_match.group(0)

    return None


async def _fetch_changelog_link_from_readme(repository_url: str) -> str | None:
    """Try to extract a changelog link from the project's README on GitHub.

    Tries README.md/rst on main/master branches sequentially.  Returns the
    extracted URL or None.
    """
    match = _GITHUB_OWNER_REPO_RE.search(repository_url)
    if not match:
        return None

    owner, repo = match.group(1), match.group(2)
    client = get_http_client()

    for filename, branch in _README_CANDIDATES:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}"
        try:
            r = await client.get(url)
            if r.status_code != 200:
                continue
            link = _extract_changelog_link(r.text)
            if link:
                return link
        except (httpx.HTTPStatusError, httpx.RequestError):
            continue

    return None


# Changelog filenames tried at the root and inside every subdirectory.
_CHANGELOG_FILENAMES: list[str] = [
    "CHANGELOG.md",
    "CHANGELOG.rst",
    "CHANGES.md",
    "CHANGES.rst",
    "HISTORY.md",
    "HISTORY.rst",
    "NEWS.md",
    "NEWS.rst",
    "changelog.md",
    "changelog.rst",
    "changes.md",
    "changes.rst",
]

# Root-level filenames tried first (covers the vast majority of packages).
_ROOT_FILENAMES = _CHANGELOG_FILENAMES

# Subdirectory prefixes searched after all root files fail or are stubs.
_SUBDIRECTORY_ROOTS: list[str] = [
    "docs/",
    "doc/",
    "doc/en/",
    "docs/en/",
]

# Doc-subdirectory paths: Cartesian product of roots × filenames.
_DOC_FILENAMES: list[str] = [f"{subdir}{name}" for subdir in _SUBDIRECTORY_ROOTS for name in _CHANGELOG_FILENAMES]


async def _try_urls_concurrently(
    client: httpx.AsyncClient,
    urls: list[str],
    sem: asyncio.Semaphore,
) -> str | None:
    """Return text of the first URL that yields valid version chunks, or None.

    Fans out all requests concurrently, capped by *sem*.  Returns as soon as
    any response contains parseable version headers; cancels remaining tasks.
    """
    if not urls:
        return None

    async def _fetch_one(url: str) -> str | None:
        async with sem:
            try:
                r = await client.get(url)
                if r.status_code == 200 and chunk_changelog_by_version(r.text):
                    return r.text
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                logger.debug("concurrent fetch failed for %s: %s", url, exc)
            return None

    tasks = [asyncio.create_task(_fetch_one(url)) for url in urls]
    result: str | None = None
    try:
        for fut in asyncio.as_completed(tasks):
            value = await fut
            if value is not None:
                result = value
                break
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return result


async def _fetch_from_github(repository_url: str) -> str:
    """Try common changelog filenames on raw.githubusercontent.com.

    Strategy:
    1. Fan out all root-level URLs (filenames × branches) concurrently.
    2. If a file returns 200 but has no version headers it is a stub —
       scan it for a GitHub blob URL and follow that URL directly.
    3. If all root files fail, repeat with doc-subdirectory paths.
    """
    match = re.search(r"github\.com[/:]([^/]+)/([^/#]+?)(?:\.git)?(?:[#/]|$)", repository_url)
    if not match:
        raise ValueError(f"Cannot parse GitHub URL: {repository_url}")

    owner, repo = match.group(1), match.group(2)
    branches = ["main", "master"]
    sem = asyncio.Semaphore(10)

    client = get_http_client()
    for filenames_group in (_ROOT_FILENAMES, _DOC_FILENAMES):
        urls = [
            f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}"
            for branch in branches
            for filename in filenames_group
        ]

        # Fast path: probe all URLs in parallel.
        result = await _try_urls_concurrently(client, urls, sem)
        if result is not None:
            return result

        # Slow path: look for stub files that embed a GitHub blob URL.
        for url in urls:
            try:
                r = await client.get(url)
                if r.status_code != 200 or chunk_changelog_by_version(r.text):
                    continue
                m = _GITHUB_BLOB_RE.search(r.text)
                if m:
                    raw_url = f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}/{m.group(4)}"
                    try:
                        r2 = await client.get(raw_url)
                        if r2.status_code == 200 and chunk_changelog_by_version(r2.text):
                            return r2.text
                    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                        logger.debug("blob redirect fetch failed for %s: %s", raw_url, exc)
            except (httpx.HTTPStatusError, httpx.RequestError):
                continue

    raise FileNotFoundError(f"No changelog found for {owner}/{repo}")


def _parse_next_link(link_header: str | None) -> str | None:
    """Extract the URL for rel="next" from a GitHub API Link header.

    Returns None if there is no next page.
    """
    if not link_header:
        return None
    m = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return m.group(1) if m else None


async def _fetch_from_github_releases(repository_url: str) -> str:
    """Fetch release notes from the GitHub Releases API.

    Follows ``Link: <next>`` pagination headers to retrieve all releases, not
    just the first 100.  Constructs changelog text from release ``body`` fields,
    skipping drafts and pre-releases.  Raises ``FileNotFoundError`` if no
    usable releases exist.  Sends an ``Authorization`` header when
    ``GITHUB_TOKEN`` is set.
    """
    match = re.search(r"github\.com[/:]([^/]+)/([^/#]+?)(?:\.git)?(?:[#/]|$)", repository_url)
    if not match:
        raise ValueError(f"Cannot parse GitHub URL: {repository_url}")

    owner, repo = match.group(1), match.group(2)
    url: str | None = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100"

    settings = get_settings()
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    all_releases: list[dict] = []
    client = get_http_client()
    while url is not None:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        all_releases.extend(response.json())
        url = _parse_next_link(response.headers.get("Link"))

    usable = [r for r in all_releases if not r.get("draft") and not r.get("prerelease")]
    if not usable:
        raise FileNotFoundError(f"No releases found for {owner}/{repo}")

    sections = [f"## {r['tag_name']}\n{r.get('body') or ''}" for r in usable]
    return "\n\n".join(sections)


# Matches a bare version number at the start of a cleaned string: 1.2.3 or 1.2
_VERSION_RE = re.compile(r"^(\d+\.\d+(?:\.\d+)?)")


def _parse_version_from_line(line: str) -> str | None:
    """Signal A+C: extract version if the line's primary purpose is naming a version.

    Strips formatting markup (##, **, [], optional single-word prefix, v-prefix),
    then checks that what remains is a version number with at most a brief suffix
    (date, dash, parenthesised date).  Long content after the version → returns None.
    """
    s = line.strip()
    # Strip markdown heading markers
    s = re.sub(r"^#{1,6}\s*", "", s).strip()
    # Strip leading/trailing bold markers
    s = re.sub(r"^\*{1,2}", "", s).strip()
    s = re.sub(r"\*{1,2}$", "", s).strip()
    # Strip leading bracket (keep closing bracket for now)
    s = re.sub(r"^\[", "", s).strip()

    # Optional single-word prefix: "Release", "Version", etc. (1–30 alpha chars)
    m = re.match(r"^([A-Za-z]\w{0,29})\s+(.*)", s)
    if m:
        s = m.group(2).strip()

    # Strip leading v/V
    if len(s) > 1 and s[0] in ("v", "V") and s[1].isdigit():
        s = s[1:]

    # Strip trailing bracket (from [3.0.0] style)
    s = re.sub(r"^\[?", "", s).strip()
    s = re.sub(r"]?", "", s, count=1).strip()

    m = _VERSION_RE.match(s)
    if not m:
        return None

    version = m.group(1)
    remainder = s[m.end() :].strip()

    # Allow: nothing, ], closing **, optional "- YYYY-MM-DD" or "(YYYY-MM-DD)"
    remainder = re.sub(r"^[]* ]+", "", remainder).strip()
    remainder = re.sub(r"^[-–]\s*\d{4}[\d\-]*\s*", "", remainder).strip()
    remainder = re.sub(r"^\(\d{4}[\d\-]*\)\s*", "", remainder).strip()

    # If more than two words of content remain, this line is not a version header
    if len(remainder.split()) > 2:
        return None

    return version


def _is_header_position(i: int, lines: list[str]) -> bool:
    """Signal B: True if line i carries header-level structural markup.

    Accepts:
    - Markdown ATX heading  (## …)
    - Bold-wrapped line     (**Release …**)
    - RST setext underline  (next line is ---/=== of sufficient length)
    - Bare version preceded by a blank line (or at start of file)
    """
    raw = lines[i]
    stripped = raw.strip()

    # ATX heading
    if re.match(r"^#{1,6}\s", raw):
        return True

    # Bold wrapper: starts with ** (but not *** which is a HR, and not * list item)
    if re.match(r"^\*{1,2}[^*\s]", stripped):
        return True

    # RST setext underline: next non-empty line is ---/=== of length ≥ 3
    if i + 1 < len(lines):
        next_line = lines[i + 1].strip()
        if re.fullmatch(r"[-=]{3,}", next_line):
            return True

    # Bare version number (possibly with date) at start of file or after blank line
    bare = stripped
    bare = re.sub(r"^v", "", bare)
    bare = re.sub(r"\s*\([\d\-]+\)\s*$", "", bare).strip()
    bare = re.sub(r"\s*[-–]\s*[\d\-]+\s*$", "", bare).strip()
    if re.fullmatch(r"\d+\.\d+(?:\.\d+)?", bare):
        if i == 0 or lines[i - 1].strip() == "":
            return True

    return False


def chunk_changelog_by_version(text: str) -> list[dict]:
    """Split changelog text into per-version chunks.

    Uses a three-signal structural approach that handles all common formats:
    - Markdown ATX headings: ## v1.0.0, ## [3.0.0], ## Release 4.1.0 - 2024-10-12
    - Bold headers: **Release 4.0.6** - 2024-03-09
    - RST setext: 2.32.5 (2025-08-18)\\n---, Version 3.1.0\\n---
    - Bare version: 1.0.0 (preceded by blank line)

    Each chunk: {"version": "2.0.0", "content": "..."}
    """
    if not text.strip():
        return []

    lines = text.splitlines()
    # Reconstruct line start offsets for slicing the original text
    offsets: list[int] = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1  # +1 for the newline

    header_positions: list[tuple[int, str, int]] = []  # (line_index, version, char_offset)
    for i, line in enumerate(lines):
        version = _parse_version_from_line(line)
        if version and _is_header_position(i, lines):
            header_positions.append((i, version, offsets[i]))

    if not header_positions:
        return []

    chunks = []
    for idx, (line_i, version, _) in enumerate(header_positions):
        # Content starts after this header line (and the RST underline if present)
        content_line = line_i + 1
        # Skip RST underline
        if content_line < len(lines) and re.fullmatch(r"[-=]{3,}", lines[content_line].strip()):
            content_line += 1
        content_start = offsets[content_line] if content_line < len(lines) else len(text)

        if idx + 1 < len(header_positions):
            content_end = header_positions[idx + 1][2]
        else:
            content_end = len(text)

        content = text[content_start:content_end].strip()
        chunks.append({"version": version, "content": content})

    return chunks


def _parse_version(v: str) -> Version:
    """Parse a version string using packaging.version."""
    return Version(v)


def filter_chunks_by_version_range(
    chunks: list[dict],
    current_version: str,
    latest_version: str,
) -> list[dict]:
    """Return chunks with versions > current and <= latest."""
    if not chunks:
        return []

    try:
        current = _parse_version(current_version)
        latest = _parse_version(latest_version)
    except InvalidVersion:
        # Fallback: simple tuple comparison
        try:
            current = tuple(int(x) for x in current_version.split("."))  # type: ignore[assignment]
            latest = tuple(int(x) for x in latest_version.split("."))  # type: ignore[assignment]
        except (ValueError, AttributeError):
            return chunks

    filtered = []
    for chunk in chunks:
        try:
            v = _parse_version(chunk["version"])
        except InvalidVersion:
            try:
                v = tuple(int(x) for x in chunk["version"].split("."))  # type: ignore[assignment]
            except (ValueError, AttributeError):
                continue

        if current < v <= latest:  # type: ignore[operator]
            filtered.append(chunk)

    return filtered


# ---------------------------------------------------------------------------
# Semantic extraction & truncation
# ---------------------------------------------------------------------------

_BREAKING_CHANGE_PATTERNS = re.compile(
    r"(?:^|\n)"
    r"(?:#+\s*|[-*]\s+)?"
    r"(?:"
    r"break(?:ing)?[\s_-]?change"
    r"|deprecat"
    r"|remov(?:ed?|ing|al)"
    r"|renam(?:ed?|ing)"
    r"|migrat(?:e|ion|ing)"
    r"|upgrade[\s_-]?guide"
    r"|backwards?[\s_-]?(?:in)?compat"
    r"|no[\s_-]?longer[\s_-]?support"
    r")",
    re.IGNORECASE,
)

# Matches the start of a markdown heading or a blank line (section boundary).
_SECTION_BOUNDARY = re.compile(r"(?:^|\n)(?=#{1,6}\s|\s*$)")


def extract_breaking_changes(chunks: list[dict]) -> list[dict]:
    """Filter chunk content to only breaking-change-related sections.

    For each chunk:
    - If breaking change patterns are found, extract the matching
      line + subsequent lines until a section boundary.
    - If no patterns match, replace content with a placeholder.
    - Version keys are always preserved.
    """
    if not chunks:
        return []

    result = []
    for chunk in chunks:
        content = chunk["content"]
        matches = list(_BREAKING_CHANGE_PATTERNS.finditer(content))

        if not matches:
            result.append({"version": chunk["version"], "content": "(no breaking changes noted)"})
            continue

        # Extract paragraphs around each match.
        extracted_sections: list[str] = []
        for m in matches:
            # Find start of the line containing the match.
            line_start = content.rfind("\n", 0, m.start()) + 1
            # Find the end of the section: next blank line or heading.
            after = content[m.end() :]
            boundary = _SECTION_BOUNDARY.search(after)
            if boundary:
                section_end = m.end() + boundary.start()
            else:
                section_end = len(content)
            section = content[line_start:section_end].strip()
            if section and section not in extracted_sections:
                extracted_sections.append(section)

        result.append({
            "version": chunk["version"],
            "content": "\n\n".join(extracted_sections) if extracted_sections else "(no breaking changes noted)",
        })

    return result


def truncate_chunks(chunks: list[dict], max_chars: int) -> tuple[list[dict], bool]:
    """Apply a hard character budget to changelog chunks.

    Walks chunks from first (newest) to last (oldest). Returns
    ``(kept_chunks, was_truncated)``.
    """
    if not chunks:
        return [], False

    kept: list[dict] = []
    budget = max_chars
    was_truncated = False

    for chunk in chunks:
        chunk_size = len(json.dumps(chunk))

        if chunk_size <= budget:
            kept.append(chunk)
            budget -= chunk_size
        elif budget > 0:
            # Fit a truncated version of this chunk.
            suffix = "... [truncated]"
            # Reserve space for the JSON envelope minus the content.
            envelope_size = len(json.dumps({"version": chunk["version"], "content": suffix}))
            available = budget - envelope_size + len(suffix)
            if available > 0:
                truncated_content = chunk["content"][:available] + suffix
            else:
                truncated_content = suffix
            kept.append({"version": chunk["version"], "content": truncated_content})
            was_truncated = True
            break
        else:
            was_truncated = True
            break

    return kept, was_truncated