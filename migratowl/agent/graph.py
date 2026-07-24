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

"""Migratowl agent graph — factory for ``langgraph.json`` / deep-agents-ui.

``langgraph.json`` points at the zero-arg :func:`graph` factory. langgraph-cli
detects a callable target and invokes it once to obtain the compiled graph, so
the sandbox manager is provisioned lazily on first access rather than at module
import time. This keeps ``import migratowl.agent.graph`` side-effect free (no
K8s I/O, no ``atexit`` registration) — the FastAPI webhook path never imports
this module and builds its own agent per scan.
"""

from __future__ import annotations

import atexit
from typing import Any

from migratowl.agent.factory import create_migratowl_agent  # noqa: F401 — re-export
from migratowl.agent.sandbox import create_sandbox_manager
from migratowl.config import Settings, get_settings
from migratowl.observability import get_invoke_config as get_invoke_config  # re-export
from migratowl.patches import apply_patches

__all__ = ["build_graph", "graph", "create_migratowl_agent", "get_invoke_config"]


def build_graph(settings: Settings | None = None) -> Any:
    """Build the Migratowl agent graph with a fresh sandbox manager.

    Applies third-party monkey-patches, provisions a ``KubernetesSandboxManager``,
    registers its shutdown at interpreter exit, and returns the compiled agent.
    Called by :func:`graph` (the ``langgraph.json`` entrypoint) and available for
    direct use in scripts/tests.
    """
    apply_patches()
    if settings is None:
        settings = get_settings()
    manager = create_sandbox_manager(settings)
    atexit.register(manager.shutdown)
    return create_migratowl_agent(manager, settings=settings)


def graph() -> Any:
    """Zero-arg graph factory for ``langgraph.json`` (``graph.py:graph``).

    langgraph-cli invokes this callable once to obtain the compiled graph.
    """
    return build_graph()
