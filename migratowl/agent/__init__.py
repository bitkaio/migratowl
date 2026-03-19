"""MigratOwl agent — deep agent with Kubernetes sandbox backend.

This module provides two ways to use the agent:

1. **Module-level ``graph``** — lazy-init singleton for ``langgraph.json``
   compatibility (deep-agents-ui).  Sandbox is created via a background
   ThreadPoolExecutor on first access.

2. **``create_migratowl_agent(sandbox)``** — factory that accepts a
   pre-initialized sandbox.  Used by the FastAPI webhook (lifespan handler
   creates the sandbox and passes it in).
"""

import atexit
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from deepagents.backends.protocol import BackendProtocol
from langchain.tools import ToolRuntime
from langchain_kubernetes import KubernetesProvider

from migratowl.agent.factory import create_migratowl_agent  # noqa: F401 — re-export
from migratowl.agent.sandbox import _blocking_init
from migratowl.config import get_settings
from migratowl.observability import get_invoke_config as get_invoke_config  # re-export
from migratowl.patches import apply_patches

apply_patches()

settings = get_settings()

logger = logging.getLogger(__name__)

# --- Sandbox lifecycle (lazy init via background thread for langgraph.json) ---

_provider: KubernetesProvider | None = None
_sandbox_future: Future[BackendProtocol] | None = None
_sandbox_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1)


def _init_sandbox() -> BackendProtocol:
    """Blocking K8s init — runs in executor thread, called once."""
    global _provider
    _provider, sandbox = _blocking_init(settings)
    atexit.register(_cleanup)
    return sandbox


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


def _k8s_backend_factory(runtime: ToolRuntime | None = None) -> BackendProtocol:
    """Return cached K8s sandbox — raises if unavailable."""
    try:
        return _get_sandbox_backend()
    except Exception as exc:
        logger.exception("Kubernetes sandbox initialization failed: %s", exc)
        raise RuntimeError("Kubernetes sandbox is required but failed to initialize.") from exc


# --- Agent graph (lazy-init singleton for langgraph.json compat) ---
graph = create_migratowl_agent(_k8s_backend_factory, settings=settings)
