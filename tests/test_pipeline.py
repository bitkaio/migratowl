# SPDX-License-Identifier: Apache-2.0

"""Tests for the deterministic scan pipeline."""

import json

from types import SimpleNamespace

from migratowl.models.schemas import Ecosystem, OutdatedDependency, ScanResult, ScanWebhookPayload


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


class _FakeTool:
    """Records ainvoke calls and returns canned output (str or callable(args) -> str)."""

    def __init__(self, output) -> None:
        self.output = output
        self.calls: list[tuple[dict, dict]] = []

    async def ainvoke(self, args: dict, config: dict | None = None) -> str:
        self.calls.append((args, config or {}))
        return self.output(args) if callable(self.output) else self.output


class _RecordingTool:
    def __init__(self, result: str, order: list[str], label: str) -> None:
        self.result, self.order, self.label = result, order, label

    async def ainvoke(self, args: dict, config: dict | None = None) -> str:
        self.order.append(self.label)
        return self.result


def _fake_tools(*, clone="Successfully cloned", deps=None, outdated=None, update="Updated 1 package(s) in main/",
                validate=None):
    from types import SimpleNamespace

    deps = deps if deps is not None else [
        {"name": "flask", "current_version": "2.0.0", "ecosystem": "python", "manifest_path": "pyproject.toml"},
    ]
    outdated = outdated if outdated is not None else [
        {"name": "flask", "current_version": "2.0.0", "latest_version": "3.1.0", "ecosystem": "python",
         "manifest_path": "pyproject.toml"},
    ]
    validate = validate or json.dumps({"steps": [{"name": "test", "exit_code": 0, "output": ""}], "passed": True})
    return SimpleNamespace(
        clone_repo=_FakeTool(clone),
        configure_registries=_FakeTool("Package registries: public defaults."),
        scan_dependencies=_FakeTool(json.dumps(deps)),
        check_outdated_deps=_FakeTool(json.dumps({"outdated": outdated, "failures": [], "warning": None})),
        copy_source=_FakeTool("Successfully copied source to /w/main"),
        update_dependencies=_FakeTool(update),
        validate_project=_FakeTool(validate),
    )


CONFIG = {"configurable": {"thread_id": "job-1"}}


class TestPrepareScan:
    async def test_registries_are_configured_before_anything_is_installed(self) -> None:
        from migratowl.pipeline import prepare_scan

        order: list[str] = []
        tools = _fake_tools()
        tools.configure_registries = _RecordingTool("Package registries: public defaults.", order, "configure")
        tools.update_dependencies = _RecordingTool("Updated 1 package(s) in main/\n  flask: OK", order, "update")

        await prepare_scan(tools, ScanWebhookPayload(repo_url="https://x/y"), CONFIG)

        assert order == ["configure", "update"]

    async def test_a_registry_config_failure_stops_the_scan(self) -> None:
        import pytest

        from migratowl.pipeline import PipelineError, prepare_scan

        tools = _fake_tools()
        tools.configure_registries = _FakeTool("Failed to configure package registries: .npmrc: permission_denied")

        with pytest.raises(PipelineError, match="registries"):
            await prepare_scan(tools, ScanWebhookPayload(repo_url="https://x/y"), CONFIG)

    async def test_runs_phases_in_order_with_thread_config(self) -> None:
        from migratowl.pipeline import prepare_scan

        tools = _fake_tools()
        prepared = await prepare_scan(tools, ScanWebhookPayload(repo_url="https://x/y", branch_name="dev"), CONFIG)

        assert tools.clone_repo.calls[0] == ({"repo_url": "https://x/y", "branch": "dev"}, CONFIG)
        assert tools.copy_source.calls[0][0] == {"folder_name": "main"}
        update_args = tools.update_dependencies.calls[0][0]
        assert update_args["folder_name"] == "main" and update_args["ecosystem"] == "python"
        assert json.loads(update_args["packages_json"])[0]["latest_version"] == "3.1.0"
        assert tools.validate_project.calls[0][0] == {"folder_name": "main", "ecosystem": "python"}
        assert [d.name for d in prepared.candidates] == ["flask"]
        assert prepared.scan_result.manifests_found == ["pyproject.toml"]
        assert prepared.validations[0].passed is True

    async def test_clone_failure_raises(self) -> None:
        import pytest

        from migratowl.pipeline import PipelineError, prepare_scan

        tools = _fake_tools(clone="Failed to clone https://x/y (exit code 128): not found")
        with pytest.raises(PipelineError, match="Failed to clone"):
            await prepare_scan(tools, ScanWebhookPayload(repo_url="https://x/y"), CONFIG)

    async def test_no_outdated_skips_copy_update_validate(self) -> None:
        from migratowl.pipeline import prepare_scan

        tools = _fake_tools(outdated=[])
        prepared = await prepare_scan(tools, ScanWebhookPayload(repo_url="https://x/y"), CONFIG)

        assert prepared.candidates == []
        assert tools.copy_source.calls == []
        assert tools.update_dependencies.calls == []
        assert tools.validate_project.calls == []

    async def test_ecosystem_filter_applies_before_registry(self) -> None:
        from migratowl.models.schemas import Ecosystem
        from migratowl.pipeline import prepare_scan

        deps = [
            {"name": "flask", "current_version": "2.0", "ecosystem": "python", "manifest_path": "pyproject.toml"},
            {"name": "react", "current_version": "17.0", "ecosystem": "nodejs", "manifest_path": "package.json"},
        ]
        tools = _fake_tools(deps=deps)
        await prepare_scan(tools, ScanWebhookPayload(repo_url="https://x/y", ecosystems=[Ecosystem.PYTHON]), CONFIG)

        sent = json.loads(tools.check_outdated_deps.calls[0][0]["dependencies_json"])
        assert [d["name"] for d in sent] == ["flask"]

    async def test_updates_all_ecosystems_before_validating(self) -> None:
        from migratowl.pipeline import prepare_scan

        order: list[str] = []
        outdated = [
            {"name": "flask", "current_version": "2.0", "latest_version": "3.0", "ecosystem": "python",
             "manifest_path": "pyproject.toml"},
            {"name": "react", "current_version": "17.0", "latest_version": "19.0", "ecosystem": "nodejs",
             "manifest_path": "package.json"},
        ]
        ok = json.dumps({"steps": [], "passed": True})
        tools = _fake_tools(outdated=outdated)
        tools.update_dependencies.output = lambda a: order.append(f"update:{a['ecosystem']}") or "Updated"
        tools.validate_project.output = lambda a: order.append(f"validate:{a['ecosystem']}") or ok

        prepared = await prepare_scan(tools, ScanWebhookPayload(repo_url="https://x/y"), CONFIG)

        assert order[:2] == ["update:nodejs", "update:python"] or order[:2] == ["update:python", "update:nodejs"]
        assert all(step.startswith("validate") for step in order[2:])
        assert {v.ecosystem for v in prepared.validations} == {"python", "nodejs"}

    async def test_records_update_failures(self) -> None:
        from migratowl.pipeline import prepare_scan

        tools = _fake_tools(update="Errors updating packages in main/\n  flask: FAILED (exit 1) — no dist\n")
        prepared = await prepare_scan(tools, ScanWebhookPayload(repo_url="https://x/y"), CONFIG)

        assert prepared.update_failures == {"flask": "no dist"}


def _prepared(candidates, validations, update_failures=None):
    from migratowl.models.schemas import ScanResult
    from migratowl.pipeline import EcosystemValidation, PreparedScan

    return PreparedScan(
        scan_result=ScanResult(all_deps=[], outdated=candidates, manifests_found=[], scan_duration_seconds=0.0),
        candidates=candidates,
        skipped=[],
        update_failures=update_failures or {},
        validations=[EcosystemValidation(**v) for v in validations],
    )


class TestPresolve:
    def test_green_minor_bump_resolved_without_llm(self) -> None:
        from migratowl.pipeline import presolve

        prepared = _prepared([_dep("requests", "2.31.0", "2.32.5")], [{"ecosystem": "python", "passed": True}])
        resolved, pending = presolve(prepared)

        assert pending == []
        assert resolved[0].dependency_name == "requests"
        assert resolved[0].is_breaking is False and resolved[0].confidence == 1.0

    def test_major_bump_unknown_version_and_update_failure_go_to_llm(self) -> None:
        from migratowl.pipeline import presolve

        prepared = _prepared(
            [_dep("flask", "2.0", "3.0"), _dep("odd", "*", "1.0"), _dep("pillow", "9.0", "9.5")],
            [{"ecosystem": "python", "passed": True}],
            update_failures={"pillow": "no matching distribution"},
        )
        resolved, pending = presolve(prepared)

        assert resolved == []
        assert [d.name for d in pending] == ["flask", "odd", "pillow"]

    def test_failure_is_per_ecosystem(self) -> None:
        from migratowl.models.schemas import Ecosystem
        from migratowl.pipeline import presolve

        prepared = _prepared(
            [_dep("requests", "2.31", "2.32"), _dep("react", "18.2", "18.3", Ecosystem.NODEJS)],
            [{"ecosystem": "python", "passed": True},
             {"ecosystem": "nodejs", "passed": False, "failed_step": "test", "output_tail": "boom"}],
        )
        resolved, pending = presolve(prepared)

        assert [r.dependency_name for r in resolved] == ["requests"]
        assert [d.name for d in pending] == ["react"]


class TestAnalysisBrief:
    def test_brief_lists_pending_failures_and_output_tail(self) -> None:
        from migratowl.pipeline import build_analysis_brief

        pending = [_dep("flask", "2.0.0", "3.1.0")]
        prepared = _prepared(
            pending,
            [{"ecosystem": "python", "passed": False, "failed_step": "test",
              "output_tail": "ImportError: cannot import name 'escape' from 'flask'"}],
            update_failures={"flask": "warning only"},
        )
        brief = build_analysis_brief(ScanWebhookPayload(repo_url="https://x/y"), prepared, pending)

        assert brief.startswith("Repository: https://x/y")
        assert "flask 2.0.0 -> 3.1.0 (python, pyproject.toml, MAJOR bump)" in brief
        assert "python: FAILED at step \"test\"" in brief
        assert "cannot import name 'escape' from 'flask'" in brief
        assert "flask: warning only" in brief


class TestReviewFixes:
    def test_java_coordinates_update_failure_parsed(self) -> None:
        from migratowl.pipeline import parse_update_failures

        summary = "Errors updating packages in main/\n  org.springframework:spring-core: FAILED (exit 1) — boom\n"

        assert parse_update_failures(summary) == {"org.springframework:spring-core": "boom"}

    def test_same_name_in_two_manifests_pending_together(self) -> None:
        from migratowl.models.schemas import Ecosystem
        from migratowl.pipeline import presolve

        minor = _dep("lodash", "^4.17.0", "4.17.21", Ecosystem.NODEJS)
        major = _dep("lodash", "^3.10.0", "4.17.21", Ecosystem.NODEJS)
        prepared = _prepared([minor, major], [{"ecosystem": "nodejs", "passed": True}])

        resolved, pending = presolve(prepared)

        assert resolved == []
        assert [d.current_version for d in pending] == ["^4.17.0", "^3.10.0"]


class TestInstalledVersionInPipeline:
    def _dep(self, current: str, installed: str | None, latest: str):
        from migratowl.models.schemas import Ecosystem, OutdatedDependency

        return OutdatedDependency(name="pkg", current_version=current, installed_version=installed,
                                  latest_version=latest, ecosystem=Ecosystem.PYTHON, manifest_path="pyproject.toml")

    def test_major_bump_uses_installed_version(self) -> None:
        from migratowl.pipeline import dependency_is_major_bump

        # ">=1.0" looks like 1.x, but 2.5.0 is installed: 2.5 → 2.6 is not a major bump.
        assert dependency_is_major_bump(self._dep(">=1.0", "2.5.0", "2.6.0")) is False
        assert dependency_is_major_bump(self._dep(">=1.0", None, "2.6.0")) is True

    def test_ranking_uses_installed_version(self) -> None:
        from migratowl.models.schemas import ScanWebhookPayload
        from migratowl.pipeline import select_candidates

        near = self._dep(">=1.0", "4.0.0", "4.1.0").model_copy(update={"name": "near"})
        far = self._dep(">=3.0", "3.0.0", "5.0.0").model_copy(update={"name": "far"})
        chosen, _ = select_candidates([near, far], ScanWebhookPayload(repo_url="r", max_deps=1))
        assert [d.name for d in chosen] == ["far"]

    def test_brief_shows_installed_version(self) -> None:
        from migratowl.models.schemas import ScanResult, ScanWebhookPayload
        from migratowl.pipeline import PreparedScan, build_analysis_brief

        dep = self._dep(">=2.0", "2.31.0", "3.0.0")
        prepared = PreparedScan(
            scan_result=ScanResult(all_deps=[], outdated=[dep], manifests_found=[], scan_duration_seconds=0),
            candidates=[dep], skipped=[],
        )
        brief = build_analysis_brief(ScanWebhookPayload(repo_url="r"), prepared, [dep])
        assert "pkg 2.31.0 (declared >=2.0) -> 3.0.0" in brief


class TestMajorBumpChangelogs:
    def _dep(self, name: str, current: str, latest: str):
        from migratowl.models.schemas import Ecosystem, OutdatedDependency

        return OutdatedDependency(name=name, current_version=current, latest_version=latest,
                                  ecosystem=Ecosystem.NODEJS, manifest_path="package.json",
                                  repository_url=f"https://github.com/x/{name}")

    def _tools(self, responses: dict[str, object]):
        import json as _json
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        async def fetch(args: dict, config=None) -> str:
            name = _json.loads(args["outdated_dep_json"])["name"]
            result = responses[name]
            if isinstance(result, Exception):
                raise result
            return _json.dumps(result)

        return SimpleNamespace(fetch_changelog=SimpleNamespace(ainvoke=AsyncMock(side_effect=fetch)))

    async def test_fetches_only_major_bumps_and_keeps_breaking_text(self) -> None:
        from migratowl.pipeline import fetch_major_changelogs

        tools = self._tools({
            "express": {"chunks": [{"version": "5.0.0", "content": "BREAKING: app.del() removed"},
                                   {"version": "4.22.0", "content": "(no breaking changes noted)"}],
                        "warnings": []},
        })
        pending = [self._dep("express", "4.21.2", "5.2.1"), self._dep("ejs-lint", "1.1.0", "1.2.0")]
        excerpts = await fetch_major_changelogs(tools, pending, {})
        assert excerpts == {"express": "5.0.0\nBREAKING: app.del() removed"}
        assert tools.fetch_changelog.ainvoke.await_count == 1  # minor bump not fetched

    async def test_fetch_failures_and_empty_changelogs_are_skipped(self) -> None:
        from migratowl.pipeline import fetch_major_changelogs

        tools = self._tools({
            "a": RuntimeError("github down"),
            "b": {"chunks": [{"version": "2.0.0", "content": "(no breaking changes noted)"}], "warnings": []},
        })
        excerpts = await fetch_major_changelogs(tools, [self._dep("a", "1.0.0", "2.0.0"), self._dep("b", "1.0.0", "2.0.0")], {})
        assert excerpts == {}

    async def test_excerpt_is_capped(self) -> None:
        from migratowl.pipeline import fetch_major_changelogs

        tools = self._tools({"a": {"chunks": [{"version": "2.0.0", "content": "x" * 10_000}], "warnings": []}})
        excerpts = await fetch_major_changelogs(tools, [self._dep("a", "1.0.0", "2.0.0")], {})
        assert len(excerpts["a"]) <= 1500

    def test_brief_includes_excerpts(self) -> None:
        from migratowl.models.schemas import ScanResult, ScanWebhookPayload
        from migratowl.pipeline import PreparedScan, build_analysis_brief

        dep = self._dep("express", "4.21.2", "5.2.1")
        prepared = PreparedScan(
            scan_result=ScanResult(all_deps=[], outdated=[dep], manifests_found=[], scan_duration_seconds=0),
            candidates=[dep], skipped=[], changelog_excerpts={"express": "5.0.0\nBREAKING: app.del() removed"},
        )
        brief = build_analysis_brief(ScanWebhookPayload(repo_url="r"), prepared, [dep])
        assert "Changelog excerpts" in brief
        assert "BREAKING: app.del() removed" in brief


class TestExcerptOrdering:
    async def test_major_release_notes_come_first(self) -> None:
        import json as _json
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from migratowl.models.schemas import Ecosystem, OutdatedDependency
        from migratowl.pipeline import fetch_major_changelogs

        chunks = [{"version": "5.2.0", "content": "bump codeql " * 200},
                  {"version": "v5.0.0", "content": "BREAKING: app.del() removed"}]
        tools = SimpleNamespace(fetch_changelog=SimpleNamespace(
            ainvoke=AsyncMock(return_value=_json.dumps({"chunks": chunks, "warnings": []}))))
        dep = OutdatedDependency(name="express", current_version="4.21.2", latest_version="5.2.1",
                                 ecosystem=Ecosystem.NODEJS, manifest_path="package.json")
        excerpts = await fetch_major_changelogs(tools, [dep], {})
        assert excerpts["express"].startswith("v5.0.0\nBREAKING: app.del() removed")


async def test_update_receives_go_module_path() -> None:
    import json as _json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from migratowl.models.schemas import ScanWebhookPayload
    from migratowl.pipeline import prepare_scan

    deps = [{"name": "github.com/x/y", "current_version": "1.0.0", "ecosystem": "go", "manifest_path": "go.mod"}]
    outdated = {"outdated": [{"name": "github.com/x/y", "current_version": "1.0.0", "latest_version": "v2.3.0",
                              "ecosystem": "go", "manifest_path": "go.mod", "module_path": "github.com/x/y/v2"}],
                "failures": [], "warning": None}
    update = AsyncMock(return_value="Updated 1 package(s) in main/\n  github.com/x/y: OK")
    tools = SimpleNamespace(
        clone_repo=SimpleNamespace(ainvoke=AsyncMock(return_value="Successfully cloned")),
        configure_registries=SimpleNamespace(ainvoke=AsyncMock(return_value="Package registries: public defaults.")),
        scan_dependencies=SimpleNamespace(ainvoke=AsyncMock(return_value=_json.dumps(deps))),
        check_outdated_deps=SimpleNamespace(ainvoke=AsyncMock(return_value=_json.dumps(outdated))),
        copy_source=SimpleNamespace(ainvoke=AsyncMock(return_value="Successfully copied")),
        update_dependencies=SimpleNamespace(ainvoke=update),
        validate_project=SimpleNamespace(ainvoke=AsyncMock(return_value=_json.dumps({"steps": [], "passed": True}))),
    )
    await prepare_scan(tools, ScanWebhookPayload(repo_url="https://github.com/o/r"), {})
    sent = _json.loads(update.await_args.args[0]["packages_json"])
    assert sent[0]["module_path"] == "github.com/x/y/v2"
    assert "version_key" in sent[0]


class TestCollectEvidence:
    """MO-65.3: the scanner gets curated and changelog rules per pending package; its result lands in evidence."""

    @staticmethod
    def _dep(name: str, current: str, latest: str, eco: str = "nodejs"):
        from migratowl.models.schemas import OutdatedDependency

        return OutdatedDependency(name=name, current_version=current, latest_version=latest,
                                  ecosystem=eco, manifest_path="package.json")

    async def test_request_and_result(self) -> None:
        from migratowl.pipeline import collect_evidence

        seen: dict = {}

        async def gather(args: dict, config=None) -> str:
            seen.update(json.loads(args["request_json"]))
            return json.dumps({"available": True, "packages": {"express": {
                "importing_files": ["app.js"], "importing_count": 1, "test_files": [], "tests_reach": False,
                "hits": [{"rule": "express5-res-json-status", "note": "res.json(status, body) was removed",
                          "file": "app.js", "line": 4, "text": "res.json(200, {})"}]}}})

        tools = SimpleNamespace(gather_evidence=SimpleNamespace(ainvoke=gather))
        evidence = await collect_evidence(
            tools, [self._dep("express", "4.21.2", "5.2.1")], {"express": "`res.jsonp(status, obj)` removed"}, {}
        )

        rules = seen["packages"][0]["rules"]
        assert seen["packages"][0]["name"] == "express" and seen["packages"][0]["ecosystem"] == "nodejs"
        assert any(r["id"] == "express5-route-path-syntax" for r in rules)
        assert any(r["id"].startswith("changelog:") for r in rules)
        assert evidence["express"].tests_reach is False
        assert evidence["express"].hits[0].line == 4

    async def test_an_unavailable_scanner_gives_no_evidence(self) -> None:
        from migratowl.pipeline import collect_evidence

        async def gather(args: dict, config=None) -> str:
            return json.dumps({"available": False, "reason": "ast-grep-py is not installed"})

        tools = SimpleNamespace(gather_evidence=SimpleNamespace(ainvoke=gather))
        assert await collect_evidence(tools, [self._dep("express", "4.0.0", "5.0.0")], {}, {}) == {}

    async def test_a_tool_error_gives_no_evidence(self) -> None:
        from migratowl.pipeline import collect_evidence

        async def gather(args: dict, config=None) -> str:
            raise RuntimeError("sandbox gone")

        tools = SimpleNamespace(gather_evidence=SimpleNamespace(ainvoke=gather))
        assert await collect_evidence(tools, [self._dep("express", "4.0.0", "5.0.0")], {}, {}) == {}

    def test_brief_shows_the_evidence(self) -> None:
        from migratowl.models.schemas import EvidenceHit, PackageEvidence, ScanWebhookPayload
        from migratowl.pipeline import PreparedScan, build_analysis_brief

        dep = self._dep("express", "4.21.2", "5.2.1")
        prepared = PreparedScan(
            scan_result=ScanResult(all_deps=[], outdated=[dep], manifests_found=[], scan_duration_seconds=0),
            candidates=[dep], skipped=[],
            evidence={"express": PackageEvidence(
                importing_files=["app.js"], importing_count=1, tests_reach=False,
                hits=[EvidenceHit(rule="express5-res-json-status", note="res.json(status, body) was removed",
                                  file="app.js", line=4, text="res.json(200, {})")])},
        )

        brief = build_analysis_brief(ScanWebhookPayload(repo_url="https://x/y"), prepared, [dep])

        assert "Code evidence" in brief
        assert "imported in 1 file(s) (app.js)" in brief
        assert "tests reach it: no" in brief
        assert "app.js:4" in brief and "res.json(status, body) was removed" in brief
