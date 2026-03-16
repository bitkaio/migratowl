"""MigratOwl agent — deep agent with Kubernetes sandbox backend."""

import atexit
import logging
from concurrent.futures import Future, ThreadPoolExecutor

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from langchain.tools import ToolRuntime
from langchain_anthropic import ChatAnthropic
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

from migratowl.agent.tools.clone import create_clone_repo_tool
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
After cloning, work within /home/user/workspace where the repository is checked out.
"""

# --- Sandbox lifecycle (eager background thread — avoids blockbuster BlockingError) ---

_provider: KubernetesProvider | None = None


def _init_sandbox() -> BackendProtocol:
    """Blocking K8s init — runs in a background thread.

    Raises on failure — the exception is stored in the Future and
    re-raised when ``_k8s_backend_factory`` calls ``result()``.
    """
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


# Fire-and-forget at import time — non-blocking.
# socket.connect runs in the worker thread (no event loop), so blockbuster ignores it.
_executor = ThreadPoolExecutor(max_workers=1)
_sandbox_future: Future[BackendProtocol] = _executor.submit(_init_sandbox)


def _cleanup() -> None:
    try:
        sandbox = _sandbox_future.result(timeout=5) if _sandbox_future.done() else None
    except Exception:
        return
    if _provider and sandbox:
        try:
            _provider.delete(sandbox_id=sandbox.id)
            logger.info("Sandbox %s deleted.", sandbox.id)
        except Exception:
            logger.warning("Failed to clean up sandbox.", exc_info=True)


def _k8s_backend_factory(runtime: ToolRuntime) -> BackendProtocol:
    """Return cached K8s sandbox — raises if unavailable.

    Waits on the background thread via Future.result(), which uses
    threading.Condition (not socket I/O) — invisible to blockbuster.
    """
    try:
        return _sandbox_future.result(timeout=120)
    except Exception as exc:
        raise RuntimeError("Kubernetes sandbox is required but failed to initialize.") from exc


# --- Agent graph ---

def _get_sandbox_backend() -> BackendProtocol:
    """Return cached sandbox backend for custom tools."""
    return _sandbox_future.result(timeout=120)


clone_repo = create_clone_repo_tool(_get_sandbox_backend, workspace_path=settings.workspace_path)

graph = create_deep_agent(
    model=ChatAnthropic(model=settings.model_name),
    system_prompt=SYSTEM_PROMPT,
    tools=[clone_repo],
    backend=_k8s_backend_factory,
)
