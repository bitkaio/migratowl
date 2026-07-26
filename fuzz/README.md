# Fuzzing

Coverage-guided [Atheris](https://github.com/google/atheris) fuzz harnesses for
MigratOwl's untrusted-input parsers. Dependency manifests and changelog text are
fetched from arbitrary upstream repositories, so malformed input is an
attacker-controlled surface — these harnesses assert the parsers fail gracefully
(defined exceptions) rather than crashing.

## Harnesses

| Harness | Targets |
|---|---|
| `fuzz_parsers.py` | `parse_requirements_txt`, `parse_pyproject_toml`, `parse_package_json`, `parse_go_mod`, `parse_cargo_toml`, `parse_pom_xml`, `parse_build_gradle`, `_parse_pep508` |
| `fuzz_changelog.py` | `chunk_changelog_by_version`, `filter_chunks_by_version_range`, `extract_breaking_changes`, `truncate_chunks` |

## Running locally

Atheris ships Linux-only wheels; run under WSL/Linux.

```bash
# Short smoke run (what CI does)
PYTHONPATH=. uv run python fuzz/fuzz_parsers.py -atheris_runs=20000 fuzz/seeds/parsers
PYTHONPATH=. uv run python fuzz/fuzz_changelog.py -atheris_runs=20000 fuzz/seeds/changelog

# Longer campaign, persisting a working corpus (gitignored)
mkdir -p fuzz/corpus/parsers
PYTHONPATH=. uv run python fuzz/fuzz_parsers.py fuzz/corpus/parsers fuzz/seeds/parsers
```

## Layout

- `seeds/` — small, curated, human-readable inputs committed to git (fuzzer starting points).
- `corpus/`, `crash-*` — generated artifacts, gitignored.

## Reproducing a crash

libFuzzer writes a `crash-<hash>` file on failure. Replay it with:

```bash
PYTHONPATH=. uv run python fuzz/fuzz_parsers.py crash-<hash>
```

Crashes found here are fixed via TDD: add a failing regression test in `tests/`
first (see `tests/test_parsers.py` / `tests/test_changelog.py`), then harden the
parser.
