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

"""Atheris fuzz harness for MigratOwl manifest parsers.

These parsers ingest dependency-manifest files cloned from arbitrary,
untrusted repositories, so malformed input is an attacker-controlled surface.
The harness feeds fuzzed input to every parser and asserts each one either
returns cleanly or raises an *expected* parse error — never an uncaught crash
(unhandled IndexError, RecursionError, infinite loop, etc.).

Run locally:
    uv run python fuzz/fuzz_parsers.py -atheris_runs=100000
    uv run python fuzz/fuzz_parsers.py fuzz/corpus/parsers   # replay a corpus
"""

import sys

import atheris

with atheris.instrument_imports():
    from migratowl import parsers

# Each parser paired with the exceptions it may legitimately raise on bad input.
# Anything outside these is a real defect the fuzzer should surface.
_TomlError = __import__("tomllib").TOMLDecodeError
import json as _json  # noqa: E402
import xml.etree.ElementTree as _stdET  # noqa: E402

from defusedxml.common import DefusedXmlException  # noqa: E402

_PARSERS = [
    (parsers.parse_requirements_txt, (ValueError,)),
    (parsers.parse_pyproject_toml, (ValueError, _TomlError)),
    (parsers.parse_package_json, (ValueError, _json.JSONDecodeError)),
    (parsers.parse_go_mod, (ValueError,)),
    (parsers.parse_cargo_toml, (ValueError, _TomlError)),
    (parsers.parse_pom_xml, (ValueError, _stdET.ParseError, DefusedXmlException)),
    (parsers.parse_build_gradle, (ValueError,)),
]


def test_one_input(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    # Route the fuzzer across parsers by consuming one selector byte.
    idx = fdp.ConsumeIntInRange(0, len(_PARSERS) - 1)
    content = fdp.ConsumeUnicodeNoSurrogates(fdp.remaining_bytes())
    parser, allowed = _PARSERS[idx]
    try:
        result = parser(content, "fuzz/manifest")
    except allowed:
        return  # expected parse failures are fine
    else:
        # Contract check: parsers must return a list.
        assert isinstance(result, list), f"{parser.__name__} returned {type(result)}"

    # Also exercise the PEP 508 helper directly (used by parse_pyproject_toml).
    try:
        parsers._parse_pep508(content)
    except ValueError:
        pass


def main() -> None:
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
