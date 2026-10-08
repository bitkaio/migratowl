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

"""Agent graph factory — builds the Migratowl agent graph."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain.agents.structured_output import ProviderStrategy
from langchain.chat_models import init_chat_model
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core.tools import BaseTool
from langchain_kubernetes import KubernetesSandboxManager

from migratowl.agent.session_graph import apply_session_injection
from migratowl.agent.subagents import create_package_analyzer_subagent
from migratowl.agent.tools.changelog import create_fetch_changelog_tool
from migratowl.agent.tools.clone import clone_tokens, create_clone_repo_tool, create_copy_source_tool
from migratowl.agent.tools.detect import create_detect_languages_tool
from migratowl.agent.tools.execute import create_execute_project_tool
from migratowl.agent.tools.manifest import create_patch_manifest_tool, create_read_manifest_tool
from migratowl.agent.tools.prepare import create_prepare_scan_tool
from migratowl.agent.tools.registries import create_configure_registries_tool
from migratowl.agent.tools.registry import create_check_outdated_tool
from migratowl.agent.tools.scan import create_scan_dependencies_tool
from migratowl.agent.tools.update import create_update_dependencies_tool
from migratowl.agent.tools.validate import create_validate_project_tool
from migratowl.config import Settings, get_settings
from migratowl.models.schemas import OutdatedCheckMode, PackageVerdicts
from migratowl.observability import _langfuse_handler
from migratowl.registries import Registries
from migratowl.registry import CheckOptions

# Only for agent-driven entrypoints (deep-agents-ui); the webhook runs the pipeline itself.
PREPARE_SCAN_HINT = """\
If you were not given a brief, call prepare_scan(repo_url, branch, max_deps) once
to produce one. Never call it twice.

"""

SYSTEM_PROMPT = """\
You are Migratowl's analysis step. Code has already done the mechanical work: the
repository is cloned to /home/user/workspace/source/, an update of every outdated
package to its latest version was attempted in /home/user/workspace/main/, and main/
was built and tested. You receive the result as a brief that starts with "Repository:".

""" + PREPARE_SCAN_HINT + """\
Your job: for each package under "Packages to analyze", decide whether updating
it breaks the project, and return one AnalysisReport per package. Apply the FIRST
rule below that matches the package.

## How to decide
1. The package is listed under "Update failures": it could not be installed at its
   latest version. Report is_breaking=true, confidence=0.9, error_summary = the
   failure detail. Do nothing else for it.
2. Validation PASSED for the package's ecosystem: the build and tests are green with
   the update, so the package is listed only because it is a major-version bump (or
   its bump size is unknown). Call fetch_changelog_tool for it. Report is_breaking=true
   only if the changelog names a breaking change that this project plausibly uses;
   otherwise is_breaking=false. Use confidence=0.9 and cite the changelog.
3. Validation FAILED for the package's ecosystem: read the output tail and give the
   package a confidence between 0.0 and 1.0 that it caused the failure:
   - the error names the package, one of its modules or APIs → 0.8 or higher
   - a large major jump with no direct mention → 0.3–0.6
   - no plausible link → below 0.3. The build stopped at the first error, so a package
     not reached by the failing run is not proven safe; a major bump keeps confidence ≥ 0.3.
   Then, still for a FAILED ecosystem only:
   - confidence ≥ {confidence_threshold}: call fetch_changelog_tool, then report
     is_breaking=true with error_summary, changelog_citation and suggested_human_fix.
   - 0 < confidence < {confidence_threshold}: delegate to the "package-analyzer" subagent
     via task() for isolated testing. Dispatch ONE package at a time, sequentially — never
     in parallel. Give it name, current_version, latest_version, ecosystem.
   - confidence = 0 only when the package cannot plausibly be involved; report it directly
     with is_breaking=false and do not delegate it.

## Important Rules
- Only call fetch_changelog_tool for packages that are a major bump or that the
  failure points to.
- To look at a manifest use read_manifest(path=<absolute path>). Do not use write_todos,
  ls, read_file, write_file, edit_file, glob, grep or execute — they are not part of this job.
- Never call the same tool twice with the same arguments.
- Never invent a changelog citation; leave changelog_citation and suggested_human_fix
  as "" when you have nothing concrete.

## Output
Return exactly one AnalysisReport per listed package, with dependency_name written
exactly as in the brief. Do not report packages that are not listed.
"""


@dataclass(frozen=True)
class MigratowlTools:
    """Migratowl's sandbox-bound tools, built once per scan and shared by pipeline and agent."""

    backend_factory: Callable[[Any], Any]
    clone_repo: BaseTool
    configure_registries: BaseTool
    copy_source: BaseTool
    detect_languages: BaseTool
    scan_dependencies: BaseTool
    check_outdated_deps: BaseTool
    update_dependencies: BaseTool
    validate_project: BaseTool
    execute_project: BaseTool
    fetch_changelog: BaseTool
    read_manifest: BaseTool
    patch_manifest: BaseTool


def build_tools(
    manager: KubernetesSandboxManager,
    *,
    settings: Settings,
    mode: OutdatedCheckMode = OutdatedCheckMode.SAFE,
    include_prerelease: bool = False,
    on_sandbox_acquired: Callable[[str], None] | None = None,
) -> MigratowlTools:
    """Build Migratowl's sandbox-bound tools once, for both the pipeline and the agent.

    ``on_sandbox_acquired`` fires once with the sandbox id when the sandbox is first
    acquired (lazily, on the first tool call, possibly on a worker thread) — it must be
    thread-safe.
    """
    backend_factory = manager._make_backend_factory()
    _acquired = False

    def get_sandbox():
        nonlocal _acquired
        sandbox = backend_factory(None)
        if not _acquired and sandbox is not None and on_sandbox_acquired is not None:
            _acquired = True
            on_sandbox_acquired(sandbox.id)
        return sandbox

    workspace_path = settings.workspace_path
    source_path = f"{workspace_path}/source"
    registries = Registries.from_settings(settings)
    return MigratowlTools(
        backend_factory=backend_factory,
        clone_repo=create_clone_repo_tool(
            get_sandbox, workspace_path=workspace_path, tokens=clone_tokens(settings)
        ),
        configure_registries=create_configure_registries_tool(get_sandbox, registries),
        copy_source=create_copy_source_tool(get_sandbox, workspace_path=workspace_path),
        detect_languages=create_detect_languages_tool(get_sandbox, workspace_path=source_path),
        scan_dependencies=create_scan_dependencies_tool(get_sandbox, workspace_path=source_path),
        check_outdated_deps=create_check_outdated_tool(
            concurrency=settings.scan_registry_concurrency,
            options=CheckOptions(
                mode=mode,
                include_prerelease=include_prerelease,
                python_version=settings.sandbox_python_version,
                registries=registries,
            ),
        ),
        update_dependencies=create_update_dependencies_tool(get_sandbox, workspace_path=workspace_path),
        validate_project=create_validate_project_tool(
            get_sandbox, workspace_path=workspace_path, max_output_chars=settings.max_output_chars
        ),
        execute_project=create_execute_project_tool(
            get_sandbox, workspace_path=workspace_path, max_output_chars=settings.max_output_chars
        ),
        fetch_changelog=create_fetch_changelog_tool(),
        read_manifest=create_read_manifest_tool(get_sandbox, workspace_path=workspace_path),
        patch_manifest=create_patch_manifest_tool(get_sandbox),
    )


def create_migratowl_agent(
    manager: KubernetesSandboxManager,
    *,
    tools: MigratowlTools | None = None,
    include_prepare_scan: bool = True,
    settings: Settings | None = None,
    mode: OutdatedCheckMode = OutdatedCheckMode.SAFE,
    include_prerelease: bool = False,
    checkpointer: Any = None,
    rate_limiter: InMemoryRateLimiter | None = None,
    on_sandbox_acquired: Callable[[str], None] | None = None,
    usage_callback: BaseCallbackHandler | None = None,
) -> Any:
    """Build the Migratowl agent graph.

    Args:
        manager: KubernetesSandboxManager that handles per-thread sandbox
            acquisition via LangGraph's create_setup_node() mechanism.
        tools: Prebuilt sandbox tools (see ``build_tools``). Built here when ``None``;
            pass them to share one tool set with the deterministic pipeline.
        include_prepare_scan: Give the agent the ``prepare_scan`` tool. ``False`` when the caller
            already ran the pipeline (webhook), so a model cannot re-run it without the payload filters.
        settings: Optional settings override; defaults to ``get_settings()``.
        mode: How to resolve the latest version — SAFE respects declared semver
            constraints, NORMAL compares against the global maximum version.
        include_prerelease: When True, pre-release versions are considered when
            determining whether a dependency is outdated.
        checkpointer: LangGraph checkpointer for durable agent state across runs
            (enables resume). ``None`` = no checkpointing.
        rate_limiter: Shared LLM rate limiter. When ``None`` a per-agent limiter
            is built — but callers running concurrent scans should inject ONE
            shared limiter so total API RPS stays bounded.
        on_sandbox_acquired: Optional callback invoked once with the sandbox id
            when the sandbox is first acquired (for durable crash-recovery
            persistence). Runs on the tool's worker thread — must be thread-safe.
    """
    if settings is None:
        settings = get_settings()

    if tools is None:
        tools = build_tools(
            manager,
            settings=settings,
            mode=mode,
            include_prerelease=include_prerelease,
            on_sandbox_acquired=on_sandbox_acquired,
        )

    agent_tools = [tools.fetch_changelog, tools.read_manifest]
    if include_prepare_scan:
        agent_tools.insert(0, create_prepare_scan_tool(tools, tail_chars=settings.analysis_tail_chars))

    # Model with rate limiter — supports anthropic, openai, and litellm via init_chat_model.
    # Prefer an injected shared limiter (bounds total API RPS across concurrent scans);
    # fall back to a per-agent limiter when none is provided.
    if rate_limiter is None:
        rate_limiter = InMemoryRateLimiter(
            requests_per_second=settings.model_rate_limit_rps,
            check_every_n_seconds=0.1,
            max_bucket_size=1,
        )

    # Determine effective model name (alias takes precedence)
    effective_model_name = settings.model_alias or settings.model_name

    # Determine provider and base_url
    # LiteLLM is OpenAI-compatible — use openai SDK but route to litellm_base_url
    if settings.model_provider == "litellm":
        sdk_provider = "openai"
        base_url = settings.litellm_base_url
    elif settings.model_provider == "anthropic":
        sdk_provider = "anthropic"
        base_url = settings.anthropic_base_url
    else:
        sdk_provider = "openai"
        base_url = settings.openai_base_url

    extra_kwargs: dict[str, Any] = {}
    if base_url:
        extra_kwargs["base_url"] = base_url

    model = init_chat_model(
        f"{sdk_provider}:{effective_model_name}",
        rate_limiter=rate_limiter,
        max_retries=8,
        # Attached to the model itself, so the package-analyzer subagent (same
        # model instance) reports its usage to the same callback.
        callbacks=[cb for cb in (_langfuse_handler, usage_callback) if cb] or None,
        **extra_kwargs,
    )

    # Subagent
    package_analyzer = create_package_analyzer_subagent(
        model=model,
        backend_factory=tools.backend_factory,
        tools=[
            tools.copy_source, tools.update_dependencies, tools.validate_project,
            tools.execute_project, tools.fetch_changelog, tools.read_manifest, tools.patch_manifest,
        ],
    )

    system_prompt = SYSTEM_PROMPT.format(confidence_threshold=settings.confidence_threshold)
    if not include_prepare_scan:
        system_prompt = system_prompt.replace(PREPARE_SCAN_HINT, "")

    # Anthropic: request native structured output (output_config.format). Left to
    # auto-detection, LangChain falls back to ToolStrategy for models it does not
    # know, which forces tool_choice="any" — Claude Sonnet 5.5, Opus 5.5 and
    # Fable 5.1 reject that with a 400. OpenAI-compatible endpoints keep the
    # automatic choice.
    response_format: Any = (
        ProviderStrategy(PackageVerdicts) if sdk_provider == "anthropic" else PackageVerdicts
    )

    return apply_session_injection(
        manager.create_agent(
            model=model,
            system_prompt=system_prompt,
            tools=agent_tools,
            subagents=[package_analyzer],
            response_format=response_format,
            checkpointer=checkpointer,
        )
    )