# Copyright 2024 bitkaio LLC
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

"""In-place session-ID injection for LangGraph compiled graphs."""

from collections.abc import AsyncIterator
from typing import Any

from migratowl.observability import inject_session_id


def apply_session_injection(graph: Any) -> Any:
    """Patch ``graph.ainvoke`` and ``graph.astream`` to auto-inject Langfuse session IDs.

    LangGraph Server places the session identifier in
    ``config["configurable"]["thread_id"]``.  Langfuse reads it from
    ``config["metadata"]["langfuse_session_id"]``.  This function patches the
    graph **in place** so the mapping happens transparently on every invocation
    without wrapping the graph in a foreign class (which would break LangGraph
    Server's ``isinstance(graph, Pregel)`` validation).

    Args:
        graph: A compiled LangGraph graph (``CompiledStateGraph`` / ``Pregel``).

    Returns:
        The same graph object, with ``ainvoke`` and ``astream`` patched.
    """
    _orig_ainvoke = graph.ainvoke
    _orig_astream = graph.astream
    _orig_astream_events = graph.astream_events

    async def _ainvoke(input: Any, config: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return await _orig_ainvoke(input, config=inject_session_id(config), **kwargs)

    async def _astream(
        input: Any, config: dict[str, Any] | None = None, **kwargs: Any
    ) -> AsyncIterator[Any]:
        async for chunk in _orig_astream(input, config=inject_session_id(config), **kwargs):
            yield chunk

    async def _astream_events(
        input: Any, config: dict[str, Any] | None = None, **kwargs: Any
    ) -> AsyncIterator[Any]:
        async for event in _orig_astream_events(input, config=inject_session_id(config), **kwargs):
            yield event

    graph.ainvoke = _ainvoke
    graph.astream = _astream
    graph.astream_events = _astream_events
    return graph