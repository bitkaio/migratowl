"""MigratOwl agent — deep agent with Kubernetes sandbox backend."""

import atexit
import logging
import os
from concurrent.futures import Future, ThreadPoolExecutor

from deepagents import create_deep_agent
from deepagents.backends.protocol import BackendProtocol
from dotenv import load_dotenv
from langchain.tools import ToolRuntime
from langchain_anthropic import ChatAnthropic
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are MigratOwl, an AI-powered dependency migration analyzer.

You have access to a sandboxed Python environment where you can:
- Write and execute Python scripts
- Install packages with pip
- Analyze codebases
- Run tests

For now, you are in early development. Help users by executing code \
in your sandbox environment to prove the system works.
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
                template_name=os.getenv("SANDBOX_TEMPLATE", "python-sandbox-template"),
                namespace=os.getenv("SANDBOX_NAMESPACE", "default"),
                connection_mode="tunnel",
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

graph = create_deep_agent(
    model=ChatAnthropic(model="claude-sonnet-4-6"),
    system_prompt=SYSTEM_PROMPT,
    backend=_k8s_backend_factory,
)
