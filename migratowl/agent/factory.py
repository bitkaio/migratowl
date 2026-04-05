"""Agent graph factory — builds the Migratowl agent graph."""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_kubernetes import KubernetesSandboxManager

from migratowl.agent.session_graph import apply_session_injection
from migratowl.agent.subagents import create_package_analyzer_subagent
from migratowl.agent.tools.changelog import create_fetch_changelog_tool
from migratowl.agent.tools.clone import create_clone_repo_tool, create_copy_source_tool
from migratowl.agent.tools.detect import create_detect_languages_tool
from migratowl.agent.tools.execute import create_execute_project_tool
from migratowl.agent.tools.manifest import create_patch_manifest_tool, create_read_manifest_tool
from migratowl.agent.tools.registry import create_check_outdated_tool
from migratowl.agent.tools.scan import create_scan_dependencies_tool
from migratowl.agent.tools.update import create_update_dependencies_tool
from migratowl.agent.tools.validate import create_validate_project_tool
from migratowl.config import Settings, get_settings
from migratowl.models.schemas import ScanAnalysisReport
from migratowl.observability import _langfuse_handler

SYSTEM_PROMPT = """\
You are Migratowl, an AI-powered dependency migration analyzer.

You operate inside a Kubernetes sandbox with a workspace laid out as:

  /home/user/workspace/
  ├── source/          # Immutable clone — NEVER executed
  ├── main/            # All deps updated to latest, executed first
  ├── <package-name>/  # Single-package isolation (created on demand)
  └── ...

## Workflow

### Phase 1: Setup
1. Clone the repo with clone_repo — this populates source/.
2. Run detect_languages on source/ to find ecosystems and default commands.
3. Run scan_dependencies on source/ to find all declared dependencies.
4. Run check_outdated_deps to identify which have newer versions.
   Result format: {{"outdated": [...], "warning": null or "..."}}.
   If warning is present, only the largest version gaps are shown.

### Phase 2: Main Analysis
5. Run copy_source("main") to create the main/ working copy.
6. Run update_dependencies("main", ecosystem, all_outdated_packages) \
to update every outdated dependency at once.
7. Run validate_project("main", ecosystem) to build and run tests.
   - Go/Rust: always compiles first (catches API-breaking dep changes), \
then runs tests if test files are detected.
   - Python: installs deps (tries .[tests] and .[test] extras before bare install), \
then runs pytest if detected.
   - Node.js: npm install, tsc --noEmit if TypeScript, then npm test if defined.

### Phase 3: Confidence Assessment
After executing main/:
- If ALL tests pass → all packages are safe. Produce AnalysisReport per package \
with is_breaking=false and confidence=1.0.
- If tests FAIL → analyze the error output and assign a confidence score (0.0–1.0) \
to each outdated package indicating how likely it caused the failure.

Confidence scoring guidelines:
- Error message directly references the package → high confidence (≥0.8)
- Large major version jump (e.g. 2.x→3.x) → moderate confidence boost
- Import/attribute errors for known package APIs → high confidence
- Generic test failures with no clear link → low confidence (<0.5)

For packages with confidence = 0 (no evidence linking them to any failure):
- If validate_project PASSED (all tests pass): the combined run already proved \
these packages don't break the build. Directly produce AnalysisReport with \
is_breaking=false and confidence=1.0 — isolation testing is unnecessary.
- If validate_project FAILED: a package absent from the error output is NOT \
automatically safe. The build stopped at the first error; packages not yet \
compiled were never reached. Apply version-gap heuristics (major bump → \
confidence ≥ 0.3) rather than assuming confidence = 0.

For packages with 0 < confidence < {{confidence_threshold}} (some signal but ambiguous):
- Delegate to the "package-analyzer" subagent via task() for isolated testing.
  Dispatch ONE package at a time, sequentially — never in parallel — to avoid \
overloading the sandbox with concurrent backend calls.
  Provide: package name, current_version, latest_version, ecosystem.

For packages with confidence ≥ {{confidence_threshold}}:
- Fetch the changelog with fetch_changelog_tool.
- Produce an AnalysisReport with error_summary, changelog_citation, and suggested_human_fix.

### Phase 4: Compile Results
Collect all AnalysisReports (from your own analysis + subagent results) \
into a final ScanAnalysisReport.

## Important Rules
- NEVER execute code in source/ — it is the immutable reference.
- Only call fetch_changelog_tool when a package causes errors or warnings.
- Per-package folders share the same sandbox — isolation is by path, not by instance.

## Sandbox Tool Restrictions

The deepagents built-in `read_file`, `edit_file`, and `execute` tools are
NOT functional in this K8s sandbox — they will return path errors or
serialization failures. Do NOT call them.

Use these Migratowl tools instead:
- Read a file: read_manifest(path=<absolute sandbox path>)
- Edit a file: patch_manifest(path=..., old_string=..., new_string=...)
- Run a command: update_dependencies or validate_project handle their own
  execution. Do not use a raw execute tool.
- Use validate_project(folder_name, ecosystem) for post-update validation.
  Only fall back to execute_project for custom commands not covered by the
  standard validation workflow.

## Multi-manifest repos

When scan_dependencies returns dependencies, each has a manifest_path field
(relative to workspace/source/). Always include manifest_path and
current_version in packages_json when calling update_dependencies:

  {{"name": "clap", "current_version": "2.33.0",
   "latest_version": "4.6.0", "manifest_path": "dotenv/Cargo.toml"}}

This allows the tool to edit the correct sub-manifest for multi-manifest
repos (Rust workspaces, monorepos, etc.).

## Tool Failure Handling

Never retry the same tool with identical arguments after it fails. On failure:
- Rust "ambiguous" error: manifest_path and current_version are missing —
  call read_manifest to inspect Cargo.toml, then retry with current_version.
- Rust version constraint error: use patch_manifest to fix the constraint
  in Cargo.toml, then call validate_project again.
- Python pip failure: skip the package and record as unresolvable.
- Python validate_project skips tests (no pytest.ini / conftest.py / tests/): \
  use execute_project with install_command="pip install -e '.[tests]'" and \
  test_command="python3 -m pytest -x --tb=short" to force the correct extras.
- patch_manifest failure: log in error_summary and continue with other packages.
"""


def create_migratowl_agent(
    manager: KubernetesSandboxManager,
    *,
    settings: Settings | None = None,
) -> Any:
    """Build the Migratowl agent graph.

    Args:
        manager: KubernetesSandboxManager that handles per-thread sandbox
            acquisition via LangGraph's create_setup_node() mechanism.
        settings: Optional settings override; defaults to ``get_settings()``.
    """
    if settings is None:
        settings = get_settings()

    backend_factory = manager._make_backend_factory()

    def get_sandbox():
        return backend_factory(None)

    workspace_path = settings.workspace_path
    source_path = f"{workspace_path}/source"

    # Tools
    clone_repo = create_clone_repo_tool(get_sandbox, workspace_path=workspace_path)
    copy_source = create_copy_source_tool(get_sandbox, workspace_path=workspace_path)
    detect_languages = create_detect_languages_tool(get_sandbox, workspace_path=source_path)
    scan_dependencies = create_scan_dependencies_tool(get_sandbox, workspace_path=source_path)
    check_outdated_deps = create_check_outdated_tool(concurrency=settings.scan_registry_concurrency)
    update_dependencies = create_update_dependencies_tool(
        get_sandbox, workspace_path=workspace_path
    )
    execute_project = create_execute_project_tool(
        get_sandbox,
        workspace_path=workspace_path,
        max_output_chars=settings.max_output_chars,
    )
    fetch_changelog = create_fetch_changelog_tool()
    read_manifest = create_read_manifest_tool(get_sandbox, workspace_path=workspace_path)
    patch_manifest = create_patch_manifest_tool(get_sandbox)
    validate_project = create_validate_project_tool(
        get_sandbox,
        workspace_path=workspace_path,
        max_output_chars=settings.max_output_chars,
    )

    tools = [
        clone_repo,
        copy_source,
        detect_languages,
        scan_dependencies,
        check_outdated_deps,
        update_dependencies,
        validate_project,
        execute_project,
        fetch_changelog,
        read_manifest,
        patch_manifest,
    ]

    # Model with rate limiter — supports anthropic and openai via init_chat_model
    rate_limiter = InMemoryRateLimiter(
        requests_per_second=settings.model_rate_limit_rps,
        check_every_n_seconds=0.1,
        max_bucket_size=1,
    )
    base_url = (
        settings.anthropic_base_url
        if settings.model_provider == "anthropic"
        else settings.openai_base_url
    )
    model = init_chat_model(
        f"{settings.model_provider}:{settings.model_name}",
        rate_limiter=rate_limiter,
        max_retries=8,
        callbacks=[_langfuse_handler] if _langfuse_handler else None,
        **({"base_url": base_url} if base_url else {}),
    )

    # Subagent
    package_analyzer = create_package_analyzer_subagent(
        model=model,
        backend_factory=backend_factory,
        tools=[
            copy_source, update_dependencies, validate_project,
            execute_project, fetch_changelog, read_manifest, patch_manifest,
        ],
    )

    system_prompt = SYSTEM_PROMPT.format(confidence_threshold=settings.confidence_threshold)

    return apply_session_injection(
        manager.create_agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            subagents=[package_analyzer],
            response_format=ScanAnalysisReport,
        )
    )
