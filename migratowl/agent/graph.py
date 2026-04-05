"""Migratowl agent — module-level graph for ``langgraph.json`` / deep-agents-ui.

The ``graph`` singleton is built eagerly at import time.  Sandbox acquisition
is deferred to the first invocation per ``thread_id`` by
``KubernetesSandboxManager.create_setup_node()``.
"""

import atexit

from migratowl.agent.factory import create_migratowl_agent  # noqa: F401 — re-export
from migratowl.agent.sandbox import create_sandbox_manager
from migratowl.config import get_settings
from migratowl.observability import get_invoke_config as get_invoke_config  # re-export
from migratowl.patches import apply_patches

apply_patches()

settings = get_settings()

_manager = create_sandbox_manager(settings)
atexit.register(_manager.shutdown)

graph = create_migratowl_agent(_manager, settings=settings)
