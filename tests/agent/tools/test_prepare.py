# SPDX-License-Identifier: Apache-2.0

"""Tests for the prepare_scan agent tool (deep-agents-ui entrypoint to the pipeline)."""

from unittest.mock import AsyncMock, MagicMock, patch

from migratowl.models.schemas import Ecosystem, OutdatedDependency, ScanResult
from migratowl.pipeline import EcosystemValidation, PreparedScan


def _prepared() -> PreparedScan:
    dep = OutdatedDependency(name="flask", current_version="2.0", latest_version="3.0", ecosystem=Ecosystem.PYTHON,
                             manifest_path="pyproject.toml")
    return PreparedScan(
        scan_result=ScanResult(all_deps=[], outdated=[dep], manifests_found=[], scan_duration_seconds=0.0),
        candidates=[dep], skipped=[],
        validations=[EcosystemValidation(ecosystem="python", passed=False, failed_step="test", output_tail="boom")],
    )


async def test_returns_brief_and_passes_thread_config() -> None:
    from migratowl.agent.tools.prepare import create_prepare_scan_tool

    with patch("migratowl.agent.tools.prepare.prepare_scan", AsyncMock(return_value=_prepared())) as mock_prep:
        tool = create_prepare_scan_tool(MagicMock(), tail_chars=100)
        out = await tool.ainvoke(
            {"repo_url": "https://x/y", "branch": "dev", "max_deps": 3},
            config={"configurable": {"thread_id": "t-1"}},
        )

    payload, config = mock_prep.await_args.args[1], mock_prep.await_args.args[2]
    assert payload.repo_url == "https://x/y" and payload.branch_name == "dev" and payload.max_deps == 3
    assert config["configurable"]["thread_id"] == "t-1"
    assert out.startswith("Repository: https://x/y")
    assert "flask 2.0 -> 3.0" in out


async def test_reports_when_nothing_needs_analysis() -> None:
    from migratowl.agent.tools.prepare import create_prepare_scan_tool

    empty = _prepared().model_copy(update={"candidates": [], "validations": []})
    with patch("migratowl.agent.tools.prepare.prepare_scan", AsyncMock(return_value=empty)):
        out = await create_prepare_scan_tool(MagicMock(), tail_chars=100).ainvoke({"repo_url": "https://x/y"})

    assert "nothing to analyze" in out.lower()


async def test_pipeline_error_returned_to_agent_not_raised() -> None:
    from migratowl.agent.tools.prepare import create_prepare_scan_tool
    from migratowl.pipeline import PipelineError

    with patch("migratowl.agent.tools.prepare.prepare_scan", AsyncMock(side_effect=PipelineError("Failed to clone x"))):
        out = await create_prepare_scan_tool(MagicMock(), tail_chars=100).ainvoke({"repo_url": "https://x/y"})

    assert out == "prepare_scan failed: Failed to clone x"


async def test_invalid_arguments_returned_to_agent_not_raised() -> None:
    from migratowl.agent.tools.prepare import create_prepare_scan_tool

    tool = create_prepare_scan_tool(MagicMock(), tail_chars=100)
    out = await tool.ainvoke({"repo_url": "https://x/y", "max_deps": 0})

    assert out.startswith("prepare_scan failed:")


async def test_brief_includes_changelog_excerpts_for_major_bumps() -> None:
    from migratowl.agent.tools.prepare import create_prepare_scan_tool

    with patch("migratowl.agent.tools.prepare.prepare_scan", AsyncMock(return_value=_prepared())), \
         patch("migratowl.agent.tools.prepare.fetch_major_changelogs",
               AsyncMock(return_value={"flask": "3.0\nRemoved flask.ext"})) as mock_fetch:
        tool = create_prepare_scan_tool(MagicMock(), tail_chars=100)
        out = await tool.ainvoke({"repo_url": "https://x/y"}, config={"configurable": {"thread_id": "t-1"}})

    assert [d.name for d in mock_fetch.await_args.args[1]] == ["flask"]
    assert "Removed flask.ext" in out


async def test_brief_includes_code_evidence() -> None:
    from migratowl.agent.tools.prepare import create_prepare_scan_tool
    from migratowl.models.schemas import PackageEvidence

    with patch("migratowl.agent.tools.prepare.prepare_scan", AsyncMock(return_value=_prepared())), \
         patch("migratowl.agent.tools.prepare.fetch_major_changelogs", AsyncMock(return_value={})), \
         patch("migratowl.agent.tools.prepare.collect_evidence",
               AsyncMock(return_value={"flask": PackageEvidence(importing_files=["app.py"], importing_count=1,
                                                                tests_reach=False)})) as mock_collect:
        tool = create_prepare_scan_tool(MagicMock(), tail_chars=100)
        out = await tool.ainvoke({"repo_url": "https://x/y"}, config={"configurable": {"thread_id": "t-1"}})

    assert [d.name for d in mock_collect.await_args.args[1]] == ["flask"]
    assert "imported in 1 file(s) (app.py)" in out
