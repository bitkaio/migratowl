"""Optional LangFuse observability integration.

Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to enable tracing.
Optionally set LANGFUSE_HOST (default: https://cloud.langfuse.com).

Usage in agent invocations:
    from migratowl.observability import get_invoke_config

    result = await graph.ainvoke(state, config=get_invoke_config(session_id="repo/owner"))
"""

import logging
from typing import Any

from migratowl.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Module-level handler — shared across main agent and subagents so that OTel
# context propagation can nest subagent LLM calls under the parent trace.
_langfuse_handler: Any = None


def create_langfuse_handler(settings: Settings | None = None) -> Any | None:
    """Return a LangFuse CallbackHandler if both API keys are configured, else None.

    Args:
        settings: Optional Settings instance. Uses get_settings() if not provided.

    Raises:
        ImportError: If keys are set but the 'langfuse' package is not installed.
    """
    if settings is None:
        settings = get_settings()

    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None

    try:
        from langfuse.langchain import CallbackHandler
    except ImportError as exc:
        raise ImportError(
            "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are configured but the "
            "'langfuse' package is not installed. Run: uv add langfuse"
        ) from exc

    logger.info("LangFuse observability enabled (host: %s)", settings.langfuse_host)
    return CallbackHandler()


def get_invoke_config(session_id: str | None = None) -> dict[str, Any]:
    """Return a LangChain runnable config for graph.ainvoke() with LangFuse tracing.

    Pass the returned dict directly as the ``config`` argument:

        result = await graph.ainvoke(state, config=get_invoke_config(session_id="..."))

    Args:
        session_id: Groups related scan traces into one LangFuse session.
            Recommended: use ``"<owner>/<repo>"`` so all scans for a repo are grouped.

    Returns:
        Config dict with ``callbacks`` and optional ``metadata``, or ``{}`` if
        LangFuse is not configured.
    """
    if _langfuse_handler is None:
        return {}

    config: dict[str, Any] = {"callbacks": [_langfuse_handler]}
    if session_id:
        config["metadata"] = {"langfuse_session_id": session_id}
    return config


def _init() -> None:
    """Initialize the module-level handler from current settings."""
    global _langfuse_handler
    _langfuse_handler = create_langfuse_handler()


_init()
