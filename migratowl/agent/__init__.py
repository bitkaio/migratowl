"""MigratOwl agent — deep agent with Kubernetes sandbox backend."""

import atexit
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain.tools import ToolRuntime
from langchain_anthropic import ChatAnthropic
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

from migratowl.agent.tools.changelog import create_fetch_changelog_tool
from migratowl.agent.tools.clone import create_clone_repo_tool, create_copy_source_tool
from migratowl.agent.tools.detect import create_detect_languages_tool
from migratowl.agent.tools.execute import create_execute_project_tool
from migratowl.agent.tools.registry import create_check_outdated_tool
from migratowl.agent.tools.scan import create_scan_dependencies_tool
from migratowl.agent.tools.update import create_update_dependencies_tool
from migratowl.config import get_settings
from migratowl.patches import apply_patches

apply_patches()

settings = get_settings()

logger = logging.getLogger(__name__)

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
6. Run update_dependencies("main", ecosystem, all_outdated_packages, install_command) \
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

For packages with confidence ≥ {confidence_threshold}:
- Fetch the changelog with fetch_changelog_tool.
- Produce an AnalysisReport with error_summary, changelog_citation, and suggested_human_fix.

For packages with confidence < {confidence_threshold}:
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
""".format(confidence_threshold=settings.confidence_threshold)  # noqa: UP032

PACKAGE_ANALYZER_PROMPT = """\
You are a dependency migration analyzer for a single package.

You are given a package name, its current and latest version, the ecosystem,
and install/test commands.

Workflow:
1. Copy source to the package folder using copy_source("{package_name}")
2. Update ONLY the specified package using update_dependencies
3. Run the project using execute_project
4. If tests fail or produce warnings, call fetch_changelog_tool to understand
   what changed. Suggest a fix citing the exact changelog section.
5. If tests pass cleanly, report is_breaking=false with high confidence.
   Do NOT call fetch_changelog_tool if there are no errors.

Return your final analysis as JSON matching:
{dependency_name, is_breaking, error_summary, changelog_citation, \
suggested_human_fix, confidence}
"""

# --- Sandbox lifecycle (lazy init via background thread) ---
# TODO: When FastAPI webhook is implemented, move sandbox init to a lifespan
# handler and pass the instance directly to create_deep_agent(backend=sandbox)
# instead of using the ThreadPoolExecutor workaround.

_provider: KubernetesProvider | None = None
_sandbox_future: Future[BackendProtocol] | None = None
_sandbox_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1)


def _init_sandbox() -> BackendProtocol:
    """Blocking K8s init — runs in executor thread, called once."""
    global _provider
    try:
        import truststore

        truststore.extract_from_ssl()
    except ImportError:
        pass

    try:
        _provider = KubernetesProvider(
            KubernetesProviderConfig(
                template_name=settings.sandbox_template,
                namespace=settings.sandbox_namespace,
                connection_mode=settings.sandbox_connection_mode,
            )
        )
        sandbox = _provider.get_or_create()
        logger.info("Kubernetes sandbox created: %s", sandbox.id)
        atexit.register(_cleanup)
        return sandbox
    finally:
        try:
            import truststore

            truststore.inject_into_ssl()
        except ImportError:
            pass


def _get_sandbox_backend() -> BackendProtocol:
    """Return cached sandbox, creating on first call via background thread.

    Uses double-checked locking to submit _init_sandbox to the executor once.
    Future.result() blocks on a threading.Condition (pure Python wait), NOT on
    socket I/O — safe to call from the async event loop.
    """
    global _sandbox_future
    if _sandbox_future is not None:
        return _sandbox_future.result(timeout=120)
    with _sandbox_lock:
        if _sandbox_future is not None:
            return _sandbox_future.result(timeout=120)
        _sandbox_future = _executor.submit(_init_sandbox)
        return _sandbox_future.result(timeout=120)


def _cleanup() -> None:
    if not (_provider and _sandbox_future and _sandbox_future.done()):
        return
    try:
        sandbox = _sandbox_future.result(timeout=5)
    except Exception:
        return
    try:
        _provider.delete(sandbox_id=sandbox.id)
        logger.info("Sandbox %s deleted.", sandbox.id)
    except Exception:
        logger.warning("Failed to clean up sandbox.", exc_info=True)


def _k8s_backend_factory(runtime: ToolRuntime) -> BackendProtocol:
    """Return cached K8s sandbox — raises if unavailable."""
    try:
        return _get_sandbox_backend()
    except Exception as exc:
        raise RuntimeError("Kubernetes sandbox is required but failed to initialize.") from exc


# --- Tool instances ---
workspace_path = settings.workspace_path
source_path = f"{workspace_path}/source"

clone_repo = create_clone_repo_tool(_get_sandbox_backend, workspace_path=workspace_path)
copy_source = create_copy_source_tool(_get_sandbox_backend, workspace_path=workspace_path)
detect_languages = create_detect_languages_tool(_get_sandbox_backend, workspace_path=source_path)
scan_dependencies = create_scan_dependencies_tool(_get_sandbox_backend, workspace_path=source_path)
check_outdated_deps = create_check_outdated_tool(concurrency=settings.scan_registry_concurrency)
update_dependencies = create_update_dependencies_tool(_get_sandbox_backend, workspace_path=workspace_path)
execute_project = create_execute_project_tool(
    _get_sandbox_backend,
    workspace_path=workspace_path,
    max_output_chars=settings.max_output_chars,
)
fetch_changelog = create_fetch_changelog_tool()

# --- Shared model (rate limiter shared across main agent and all subagents) ---
_rate_limiter = InMemoryRateLimiter(
    requests_per_second=settings.model_rate_limit_rps,
    check_every_n_seconds=0.1,
    max_bucket_size=1,
)
_model = ChatAnthropic(model=settings.model_name, rate_limiter=_rate_limiter, max_retries=8)

# --- Subagent config ---
package_analyzer = {
    "name": "package-analyzer",
    "description": (
        "Analyzes a single package upgrade in isolation. Copies source, "
        "updates only that package, runs tests, optionally fetches changelog, "
        "and returns an AnalysisReport."
    ),
    "system_prompt": PACKAGE_ANALYZER_PROMPT,
    "tools": [copy_source, update_dependencies, execute_project, fetch_changelog],
    "model": _model,
}

# --- Agent graph ---
graph = create_deep_agent(
    model=_model,
    system_prompt=SYSTEM_PROMPT,
    tools=[
        clone_repo,
        copy_source,
        detect_languages,
        scan_dependencies,
        check_outdated_deps,
        update_dependencies,
        execute_project,
        fetch_changelog,
    ],
    backend=_k8s_backend_factory,
    subagents=[package_analyzer],
)
