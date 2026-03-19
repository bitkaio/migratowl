"""Agent graph factory — builds the MigratOwl agent graph."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain.chat_models import init_chat_model
from langchain.tools import ToolRuntime
from langchain_core.rate_limiters import InMemoryRateLimiter

from migratowl.agent.session_graph import apply_session_injection
from migratowl.agent.subagents import create_package_analyzer_subagent
from migratowl.agent.tools.changelog import create_fetch_changelog_tool
from migratowl.agent.tools.clone import create_clone_repo_tool, create_copy_source_tool
from migratowl.agent.tools.detect import create_detect_languages_tool
from migratowl.agent.tools.execute import create_execute_project_tool
from migratowl.agent.tools.registry import create_check_outdated_tool
from migratowl.agent.tools.scan import create_scan_dependencies_tool
from migratowl.agent.tools.update import create_update_dependencies_tool
from migratowl.config import Settings, get_settings
from migratowl.observability import _langfuse_handler

SYSTEM_PROMPT = """\
You are MigratOwl, an AI-powered dependency migration analyzer.

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
7. Run execute_project("main", install_command, test_command) to install and run tests.

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

For packages with confidence ≥ {{confidence_threshold}}:
- Fetch the changelog with fetch_changelog_tool.
- Produce an AnalysisReport with error_summary, changelog_citation, and suggested_human_fix.

For packages with confidence < {{confidence_threshold}}:
- Delegate to the "package-analyzer" subagent via task() for isolated testing.
  Provide: package name, current_version, latest_version, ecosystem, \
install_command, test_command.

### Phase 4: Compile Results
Collect all AnalysisReports (from your own analysis + subagent results) \
into a final ScanAnalysisReport.

## Important Rules
- NEVER execute code in source/ — it is the immutable reference.
- Only call fetch_changelog_tool when a package causes errors or warnings.
- Per-package folders share the same sandbox — isolation is by path, not by instance.
"""


def create_migratowl_agent(
    sandbox_or_factory: BackendProtocol | Callable[..., BackendProtocol],
    *,
    settings: Settings | None = None,
) -> Any:
    """Build the MigratOwl agent graph.

    Args:
        sandbox_or_factory: Either a concrete sandbox (from lifespan init)
            or a callable that returns one (for lazy langgraph.json init).
        settings: Optional settings override; defaults to ``get_settings()``.
    """
    if settings is None:
        settings = get_settings()

    # Normalize to callables for tool factories and deepagents backend
    if callable(sandbox_or_factory) and not isinstance(sandbox_or_factory, BackendProtocol):
        get_sandbox = sandbox_or_factory
        backend_factory = sandbox_or_factory
    else:
        sandbox = sandbox_or_factory

        def get_sandbox() -> BackendProtocol:
            return sandbox

        def backend_factory(runtime: ToolRuntime) -> BackendProtocol:
            return sandbox

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

    tools = [
        clone_repo,
        copy_source,
        detect_languages,
        scan_dependencies,
        check_outdated_deps,
        update_dependencies,
        execute_project,
        fetch_changelog,
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
        tools=[copy_source, update_dependencies, execute_project, fetch_changelog],
    )

    system_prompt = SYSTEM_PROMPT.format(confidence_threshold=settings.confidence_threshold)

    return apply_session_injection(
        create_deep_agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            backend=backend_factory,
            subagents=[package_analyzer],
        )
    )
