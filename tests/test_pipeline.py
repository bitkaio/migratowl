# SPDX-License-Identifier: Apache-2.0

"""Tests for the deterministic scan pipeline."""

import json

from migratowl.models.schemas import Ecosystem, OutdatedDependency, ScanWebhookPayload


def _dep(name: str, current: str, latest: str, eco: Ecosystem = Ecosystem.PYTHON) -> OutdatedDependency:
    return OutdatedDependency(
        name=name, current_version=current, latest_version=latest, ecosystem=eco, manifest_path="pyproject.toml"
    )


class TestMajorBump:
    def test_strips_constraint_operators(self) -> None:
        from migratowl.pipeline import is_major_bump, parse_major

        assert parse_major(">=3.2.10") == 3
        assert parse_major("^2.0") == 2
        assert is_major_bump(">=3.2.10", "4.0.1") is True
        assert is_major_bump("2.31.0", "2.32.5") is False

    def test_unparseable_is_unknown_not_false(self) -> None:
        from migratowl.pipeline import is_major_bump

        assert is_major_bump("*", "1.0.0") is None
        assert is_major_bump("", "1.0.0") is None


class TestSelectCandidates:
    def test_caps_at_max_deps_largest_gap_first(self) -> None:
        from migratowl.pipeline import select_candidates

        outdated = [_dep("a", "1.0", "1.5"), _dep("b", "1.0", "4.0"), _dep("c", "1.0", "2.0")]
        payload = ScanWebhookPayload(repo_url="https://x/y", max_deps=2)

        candidates, skipped = select_candidates(outdated, payload)

        assert [d.name for d in candidates] == ["b", "c"]
        assert skipped == ["a"]

    def test_exclude_and_check_deps_case_insensitive(self) -> None:
        from migratowl.pipeline import select_candidates

        outdated = [_dep("Django", "2.2", "5.0"), _dep("requests", "2.0", "2.5"), _dep("click", "7.0", "8.0")]
        payload = ScanWebhookPayload(repo_url="https://x/y", exclude_deps=["django"], check_deps=["Requests", "DJANGO"])

        candidates, skipped = select_candidates(outdated, payload)

        assert [d.name for d in candidates] == ["requests"]
        assert skipped == []


class TestParseUpdateFailures:
    def test_extracts_failed_packages_only(self) -> None:
        from migratowl.pipeline import parse_update_failures

        summary = (
            "Errors updating packages in main/\n"
            "  requests: OK\n"
            "  pillow: FAILED (exit 1) — error: no matching distribution\n"
            "  (go mod tidy): FAILED (exit 1) — boom\n"
        )

        assert parse_update_failures(summary) == {"pillow": "error: no matching distribution"}


class TestSummarizeValidation:
    def test_passed(self) -> None:
        from migratowl.pipeline import summarize_validation

        raw = json.dumps({"steps": [{"name": "test", "exit_code": 0, "output": "ok"}], "passed": True})
        v = summarize_validation("python", raw, tail_chars=100)

        assert v.passed is True and v.failed_step is None and v.output_tail == ""

    def test_failed_keeps_tail_of_failing_step(self) -> None:
        from migratowl.pipeline import summarize_validation

        out = "noise " * 50 + "ImportError: cannot import name 'x' from 'flask'"
        raw = json.dumps({
            "steps": [
                {"name": "install", "exit_code": 0, "output": ""},
                {"name": "test", "exit_code": 1, "output": out},
            ],
            "passed": False,
        })
        v = summarize_validation("python", raw, tail_chars=60)

        assert v.passed is False
        assert v.failed_step == "test"
        assert v.output_tail.endswith("cannot import name 'x' from 'flask'")
        assert len(v.output_tail) == 60

    def test_non_json_output_is_a_failure(self) -> None:
        from migratowl.pipeline import summarize_validation

        v = summarize_validation("python", "Error: sandbox exploded", tail_chars=100)

        assert v.passed is False
        assert v.output_tail == "Error: sandbox exploded"
