#!/usr/bin/env python3
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

"""Atheris fuzz harness for MigratOwl changelog/version parsing.

``chunk_changelog_by_version`` and the version-range helpers ingest changelog
text fetched from arbitrary upstream releases (untrusted). This harness feeds
fuzzed text through the chunking + filtering + extraction pipeline and asserts
it never crashes uncaught.

Run locally:
    uv run python fuzz/fuzz_changelog.py -atheris_runs=100000
    uv run python fuzz/fuzz_changelog.py fuzz/corpus/changelog
"""

import sys

import atheris

with atheris.instrument_imports():
    from migratowl import changelog


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    # First slice drives version strings; the remainder is changelog text.
    current = fdp.ConsumeUnicodeNoSurrogates(fdp.ConsumeIntInRange(0, 32))
    latest = fdp.ConsumeUnicodeNoSurrogates(fdp.ConsumeIntInRange(0, 32))
    text = fdp.ConsumeUnicodeNoSurrogates(fdp.remaining_bytes())

    # 1. Chunking must never crash and must return a list of dicts.
    chunks = changelog.chunk_changelog_by_version(text)
    assert isinstance(chunks, list)
    for c in chunks:
        assert "version" in c and "content" in c

    # 2. Version-range filtering (handles InvalidVersion internally via fallback).
    filtered = changelog.filter_chunks_by_version_range(chunks, current, latest)
    assert isinstance(filtered, list)

    # 3. Breaking-change extraction + truncation over the chunks.
    breaking = changelog.extract_breaking_changes(chunks)
    assert isinstance(breaking, list)
    changelog.truncate_chunks(chunks, fdp.ConsumeIntInRange(0, 10000))


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
