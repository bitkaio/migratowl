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

__all__ = ["graph", "get_invoke_config"]