"""MigratOwl agent — deep agent with Kubernetes sandbox backend."""

import atexit
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain.tools import ToolRuntime
from langchain_anthropic import ChatAnthropic
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

from migratowl.agent.tools.clone import create_clone_repo_tool
from migratowl.agent.tools.detect import create_detect_languages_tool
from migratowl.config import get_settings

settings = get_settings()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are MigratOwl, an AI-powered dependency migration analyzer.

You have access to a sandboxed Python environment where you can:
- Clone public Git repositories using the clone_repo tool
- Write and execute Python scripts
- Install packages with pip
- Analyze codebases
- Run tests

When given a repository URL and branch, clone it first using clone_repo. \
After cloning, use detect_languages to identify the programming languages \
and frameworks used in the repository. \
Work within /home/user/workspace where the repository is checked out.
"""

# --- Sandbox lifecycle (lazy init via background thread) ---
# TODO: When FastAPI webhook is implemented, move sandbox init to a lifespan handler and pass the instance directly to create_deep_agent(backend=sandbox) instead of using the ThreadPoolExecutor workaround.

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


clone_repo = create_clone_repo_tool(_get_sandbox_backend, workspace_path=settings.workspace_path)
detect_languages = create_detect_languages_tool(_get_sandbox_backend, workspace_path=settings.workspace_path)

graph = create_deep_agent(
    model=ChatAnthropic(model=settings.model_name),
    system_prompt=SYSTEM_PROMPT,
    tools=[clone_repo, detect_languages],
    backend=_k8s_backend_factory,
)
