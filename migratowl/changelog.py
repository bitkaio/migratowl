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
from migratowl.registry import forge_repo_url

logger = logging.getLogger(__name__)


async def fetch_changelog(
    changelog_url: str | None,
    repository_url: str | None,
    dep_name: str,
    current_version: str | None = None,
    latest_version: str | None = None,
) -> tuple[str, list[str]]:
    """Fetch changelog text, trying changelog_url first, then GitHub raw fallback.

    ``current_version`` lets the GitHub Releases strategy stop paging once it has reached
    releases the project is already on.

    Returns (text, warnings) where warnings is a list of diagnostic messages
    explaining why the changelog could not be fetched (empty on success).
    """
    # A "changelog" link that is only the repository page (e.g. github.com/o/r#history)
    # renders as HTML noise; use it as the repository and look for the real file.
    repo_page = forge_repo_url(changelog_url) or _github_releases_repo(changelog_url)
    if repo_page:
        repository_url = repository_url or repo_page
        changelog_url = None

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
        # A link to the GitHub releases page ("Latest Release") is served by the Releases API below.
        if readme_link and readme_link != changelog_url and not _github_releases_repo(readme_link):
            try:
                return await _fetch_from_url(readme_link), []
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError, FileNotFoundError) as exc:
                logger.debug("readme changelog link fetch failed for %s: %s", dep_name, exc)

    if repository_url:
        settings = get_settings()
        # With a token: API is cheap (5 000 req/hr) → try it before slow file probing.
        # Without token: preserve quota (60 req/hr) → file probing first, API last.
        repo = repository_url

        async def from_releases() -> str:
            return await _fetch_from_github_releases(repo, current_version)

        async def from_files() -> str:
            return await _fetch_from_github(repo, current_version, latest_version)

        ordered = [from_releases, from_files] if settings.github_token else [from_files, from_releases]
        for strategy in ordered:
            try:
                return await strategy(), []
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError, FileNotFoundError) as exc:
                logger.debug("strategy %s failed for %s: %s", strategy.__name__, dep_name, exc)

    return "", [f"Could not fetch changelog for {dep_name}"]


async def _fetch_from_url(url: str) -> str:
    """Fetch raw text from a URL with redirect following.

    If the response is HTML, strips it to plain text with html2text and checks
    for parseable version headers.  Raises ValueError if no version headers are
    found after stripping (triggers the GitHub raw-file fallback).
    """
    blob = _GITHUB_BLOB_RE.match(url)
    if blob:
        url = f"https://raw.githubusercontent.com/{blob.group(1)}/{blob.group(2)}/{blob.group(3)}/{blob.group(4)}"
    client = get_http_client()
    response = await client.get(url)
    response.raise_for_status()
    text = response.text
    is_html = text.lstrip().startswith(("<", "<!DOCTYPE", "<!doctype"))
    if is_html and _GITHUB_WEB_HOST.match(url):
        # GitHub's own pages (releases, repository views) are site chrome, not changelogs.
        raise ValueError(f"GitHub web page, not a changelog: {url}")
    if is_html:
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

# A changelog file this short with no versions is treated as a pointer to the real one.
_STUB_MAX_CHARS = 2000

_BARE_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")

_README_CANDIDATES = [
    ("README.md", "HEAD"),
    ("README.rst", "HEAD"),
]

_GITHUB_WEB_HOST = re.compile(r"^https?://(?:www\.)?github\.com/", re.IGNORECASE)
_GITHUB_RELEASES_PAGE = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/#?\s]+)/([^/#?\s]+)/releases(?:[/?#].*)?$", re.IGNORECASE
)


def _github_releases_repo(url: str | None) -> str | None:
    """``https://github.com/o/r`` when ``url`` is that repository's releases page (or one release), else None."""
    m = _GITHUB_RELEASES_PAGE.match((url or "").strip())
    return f"https://github.com/{m.group(1)}/{m.group(2)}" if m else None

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


# Per-major release-notes files (ejs: RELEASE_NOTES_v5.md); {n} is the major version.
_PER_MAJOR_FILENAMES: list[str] = [
    "RELEASE_NOTES_v{n}.md",
    "RELEASE-NOTES-v{n}.md",
    "RELEASE_NOTES_{n}.md",
    "release-notes/v{n}.md",
    "docs/release-notes/v{n}.md",
    "UPGRADING_v{n}.md",
    "MIGRATION_v{n}.md",
]
_MAX_PER_MAJOR_PROBES = 6  # majors probed at most, newest first


def _major(version: str | None) -> int | None:
    m = re.search(r"\d+", _bound_version(version or ""))
    return int(m.group(0)) if m else None


async def _fetch_per_major_notes(
    client: httpx.AsyncClient, owner: str, repo: str, current_version: str, latest_version: str
) -> str | None:
    """Release-notes files named after a major in (current, latest], as one changelog text."""
    cur, new = _major(current_version), _major(latest_version)
    if cur is None or new is None or new <= cur:
        return None
    majors = list(range(new, cur, -1))[:_MAX_PER_MAJOR_PROBES]
    sem = asyncio.Semaphore(10)

    async def probe(major: int, template: str) -> tuple[int, str] | None:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{template.format(n=major)}"
        async with sem:
            try:
                r = await client.get(url)
            except (httpx.HTTPStatusError, httpx.RequestError):
                return None
        return (major, r.text) if r.status_code == 200 and r.text.strip() else None

    found = await asyncio.gather(*(probe(m, t) for m in majors for t in _PER_MAJOR_FILENAMES))
    by_major: dict[int, str] = {}
    for hit in found:
        if hit and hit[0] not in by_major:
            by_major[hit[0]] = hit[1]
    if not by_major:
        return None
    sections = []
    for major in sorted(by_major, reverse=True):
        text = by_major[major]
        title = next((line for line in text.splitlines() if line.strip()), "")
        named = re.search(rf"\b({major}\.\d+(?:\.\d+)?)\b", title)
        sections.append(f"## {named.group(1) if named else f'{major}.0.0'}\n{text.strip()}")
    return "\n\n".join(sections)


async def _fetch_from_github(
    repository_url: str, current_version: str | None = None, latest_version: str | None = None
) -> str:
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
    # HEAD is the repository's default branch, whatever it is called (main, master, develop, trunk ...).
    branches = ["HEAD"]
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
                elif len(r.text) <= _STUB_MAX_CHARS and (moved := _BARE_URL_RE.search(r.text)):
                    # Short stub pointing elsewhere ("History has moved to: https://...").
                    try:
                        return await _fetch_from_url(moved.group(0).rstrip(".,;"))
                    except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as exc:
                        logger.debug("moved-to changelog fetch failed for %s: %s", moved.group(0), exc)
            except (httpx.HTTPStatusError, httpx.RequestError):
                continue

    if current_version and latest_version:
        per_major = await _fetch_per_major_notes(client, owner, repo, current_version, latest_version)
        if per_major:
            return per_major

    raise FileNotFoundError(f"No changelog found for {owner}/{repo}")


def _parse_next_link(link_header: str | None) -> str | None:
    """Extract the URL for rel="next" from a GitHub API Link header.

    Returns None if there is no next page.
    """
    if not link_header:
        return None
    m = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return m.group(1) if m else None


_TAG_VERSION = re.compile(r"\d+(?:\.\d+)+")
# A release body that only restates its version ("Version 7.0.1", "v7.0.1", "Release 5.0.2").
_VERSION_ONLY_BODY = re.compile(r"^\s*(?:release|version)?\s*v?\d+(?:\.\d+)*\S*\s*$", re.IGNORECASE)


def _has_notes(body: str | None) -> bool:
    return bool(body and body.strip() and not _VERSION_ONLY_BODY.match(body))


def _tag_reached(tag: str, current: Version | tuple[int, ...] | None) -> bool:
    """True when ``tag`` names a version at or below ``current`` (tags without a version never do)."""
    if current is None or not (m := _TAG_VERSION.search(tag)):
        return False
    version = _coerce_comparable(m.group(0))
    if version is None or type(version) is not type(current):
        return False
    return version <= current  # type: ignore[operator]


async def _fetch_from_github_releases(repository_url: str, current_version: str | None = None) -> str:
    """Fetch release notes from the GitHub Releases API.

    Follows ``Link: <next>`` pagination headers, newest releases first. With a
    ``current_version`` it stops after the page that reaches that version: older
    releases cannot be part of the upgrade, and large repos have hundreds.
    Constructs changelog text from release ``body`` fields, skipping drafts and
    pre-releases.  Raises ``FileNotFoundError`` if no usable releases exist.
    Sends an ``Authorization`` header when ``GITHUB_TOKEN`` is set.
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

    current = _coerce_comparable(_bound_version(current_version)) if current_version else None
    all_releases: list[dict] = []
    client = get_http_client()
    while url is not None:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        page = response.json()
        all_releases.extend(page)
        if any(_tag_reached(str(r.get("tag_name", "")), current) for r in page):
            break
        url = _parse_next_link(response.headers.get("Link"))

    usable = [
        r for r in all_releases
        if not r.get("draft") and not r.get("prerelease") and _has_notes(r.get("body"))
    ]
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

    # Allow: nothing, ], closing **, a link target "[1.2.3](compare-url)", optional "- YYYY-MM-DD" or "(YYYY-MM-DD)"
    remainder = re.sub(r"^[]* ]+", "", remainder).strip()
    remainder = re.sub(r"^\(https?://[^)\s]*\)", "", remainder).strip()
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


def _coerce_comparable(version_str: str) -> Version | tuple[int, ...] | None:
    """Return a comparable version: a packaging Version, or a numeric tuple
    fallback, or None if neither works. Callers must only compare values of
    the same kind (Version-vs-Version or tuple-vs-tuple)."""
    try:
        return _parse_version(version_str)
    except InvalidVersion:
        try:
            return tuple(int(x) for x in version_str.split("."))
        except (ValueError, AttributeError):
            return None


_BOUND_PREFIX = re.compile(r"^[\s>=<^~!v]+")


def _bound_version(raw: str) -> str:
    """``>=5.9,<6`` → ``5.9``: the first segment of a constraint without its operator."""
    return _BOUND_PREFIX.sub("", (raw or "").split(",")[0]).strip()


def filter_chunks_by_version_range(
    chunks: list[dict],
    current_version: str,
    latest_version: str,
) -> list[dict]:
    """Return chunks with versions > current and <= latest."""
    if not chunks:
        return []

    # Bounds may be declared constraints (">=5.9", "^4.21.2"); compare their base version.
    current = _coerce_comparable(_bound_version(current_version))
    latest = _coerce_comparable(_bound_version(latest_version))
    # If the range bounds are unusable, don't filter — return everything.
    if current is None or latest is None or type(current) is not type(latest):
        return chunks

    filtered = []
    for chunk in chunks:
        v = _coerce_comparable(chunk["version"])
        if v is None or type(v) is not type(current):
            # Unparseable or incomparable with the bounds — skip this chunk.
            continue
        # v, current, latest are all the same concrete type here (both Version
        # or both tuple), so the ordering comparison is well-defined. mypy can't
        # narrow the union across the runtime type() guard, hence the ignore.
        if current < v <= latest:  # type: ignore[operator]
            filtered.append(chunk)

    return filtered


# ---------------------------------------------------------------------------
# Semantic extraction & truncation
# ---------------------------------------------------------------------------

_BREAKING_CHANGE_PATTERNS = re.compile(
    r"(?:^|\n)[ \t]*"  # indented list items
    r"(?:#+\s*|[-*]\s+)?"
    r"(?:#\d+,?\s*)?"  # issue reference prefix ("#2490, breaking: ...")
    r"(?:\[[^\]\n]+\]:?\s*)?"  # platform/scope prefix ("[Windows]: ...")
    r"[*_]{0,2}"  # bold/italic heading markers (**Backward incompatible changes**)
    r"(?:"
    r"break(?:ing)?[\s_-]?change"
    r"|breaking\s*:"  # "* breaking: ..." item labels (psutil)
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

# Lines that say what changed for a user, looser than the section-start patterns above.
_CHANGE_WORDS = re.compile(
    r"remov|deprecat|renam|breaking|incompatib|no[\s_-]?longer|drop(?:s|ped|ping)?\b|replac|migrat|"
    r"now\s+(?:throws|requires|returns|rejects|defaults)|default(?:s|ed|ing)?\s+to|behaviou?r",
    re.IGNORECASE,
)
# GitHub's auto-generated "What's Changed" items: "* title by @user in https://github.com/o/r/pull/12".
_PR_LINE = re.compile(r"\bby\s+@\S+\s+in\s+https?://\S+/pull/\d+|\sin\s+https?://\S+/pull/\d+\s*$")
# Excerpts are cut to a few hundred characters downstream; only text longer than this needs ranking.
_RANK_ABOVE_CHARS = 1500


def _rank_lines(text: str) -> str:
    """Order a long section so the lines that matter survive a cut from the top.

    Hand-written lines naming a removal, rename or behaviour change come first, then
    auto-generated pull-request lines that do, then the remaining prose, then the other
    pull-request lines. Nothing is dropped; short sections keep their order.
    """
    if len(text) <= _RANK_ABOVE_CHARS:
        return text
    tiers: list[list[str]] = [[], [], [], []]
    for line in text.splitlines():
        if not line.strip():
            continue
        is_pr = bool(_PR_LINE.search(line))
        is_key = bool(_CHANGE_WORDS.search(line))
        tiers[(0 if is_key else 2) if not is_pr else (1 if is_key else 3)].append(line)
    return "\n".join(line for tier in tiers for line in tier)


# Matches the start of a markdown heading or a blank line (section boundary).
_SECTION_BOUNDARY = re.compile(r"(?:^|\n)(?=#{1,6}\s|\s*$)")


def merge_duplicate_versions(chunks: list[dict]) -> list[dict]:
    """One chunk per version, in first-seen order; non-empty bodies joined, longest first.

    A release page can name a version twice (title and tag), and a changelog can repeat a header.
    """
    order: list[str] = []
    bodies: dict[str, list[str]] = {}
    for chunk in chunks:
        version = str(chunk.get("version", ""))
        if version not in bodies:
            order.append(version)
            bodies[version] = []
        content = str(chunk.get("content", ""))
        if content.strip():
            bodies[version].append(content.strip())
    return [
        {"version": v, "content": "\n\n".join(sorted(bodies[v], key=len, reverse=True))}
        for v in order
    ]


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
        covered_until = 0
        for m in matches:
            # A match inside a section already taken (every item of a pull-request list matches) adds nothing.
            if m.start() < covered_until:
                continue
            # Find start of the line containing the match.
            line_start = content.rfind("\n", 0, m.start()) + 1
            # Find the end of the section: next blank line or heading.
            after = content[m.end() :]
            boundary = _SECTION_BOUNDARY.search(after)
            if boundary:
                section_end = m.end() + boundary.start()
            else:
                section_end = len(content)
            covered_until = section_end
            section = content[line_start:section_end].strip()
            if section and section not in extracted_sections:
                extracted_sections.append(section)

        # Lines elsewhere in the notes that name a removal or behaviour change but do not start a
        # section ("- **Node.js support**: Dropped support for Node < 18").
        taken = {line.strip() for section in extracted_sections for line in section.splitlines()}
        extra = [
            line for line in content.splitlines()
            if line.strip() and line.strip() not in taken and _CHANGE_WORDS.search(line)
        ]
        if extra and len(content) > _RANK_ABOVE_CHARS:
            extracted_sections.append("\n".join(extra))

        result.append({
            "version": chunk["version"],
            "content": _rank_lines("\n\n".join(extracted_sections))
            if extracted_sections
            else "(no breaking changes noted)",
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