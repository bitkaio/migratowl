"""In-place session-ID injection for LangGraph compiled graphs."""

from typing import Any, AsyncIterator

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
