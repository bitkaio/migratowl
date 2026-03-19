"""Tests for the session-aware graph injection."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from migratowl.agent.session_graph import apply_session_injection


class _FakeGraph:
    """Minimal graph-like object for testing (not a MagicMock so __class__ works)."""

    def __init__(self):
        self.ainvoke = AsyncMock(return_value={"result": "ok"})
        self.some_attr = "original"

    async def astream(self, input, config=None, **kwargs):
        yield {"chunk": 1}

    async def astream_events(self, input, config=None, **kwargs):
        yield {"event": "on_chain_start", "data": {}}


class TestApplySessionInjection:
    @pytest.mark.asyncio
    async def test_ainvoke_injects_thread_id_as_session_id(self) -> None:
        graph = _FakeGraph()
        orig_ainvoke = graph.ainvoke
        apply_session_injection(graph)

        config = {"configurable": {"thread_id": "thread-123"}}
        with patch("migratowl.agent.session_graph.inject_session_id") as mock_inject:
            mock_inject.side_effect = lambda c: {
                **c,
                "metadata": {"langfuse_session_id": "thread-123"},
            }
            await graph.ainvoke({"input": "x"}, config=config)

        mock_inject.assert_called_once_with(config)
        injected_config = orig_ainvoke.call_args.kwargs["config"]
        assert injected_config["metadata"]["langfuse_session_id"] == "thread-123"

    @pytest.mark.asyncio
    async def test_ainvoke_forwards_input_unchanged(self) -> None:
        graph = _FakeGraph()
        orig_ainvoke = graph.ainvoke
        apply_session_injection(graph)

        with patch("migratowl.agent.session_graph.inject_session_id", side_effect=lambda c: c):
            await graph.ainvoke({"repo": "owner/repo"}, config={})

        assert orig_ainvoke.call_args.args[0] == {"repo": "owner/repo"}

    @pytest.mark.asyncio
    async def test_ainvoke_handles_none_config(self) -> None:
        graph = _FakeGraph()
        orig_ainvoke = graph.ainvoke
        apply_session_injection(graph)

        with patch("migratowl.agent.session_graph.inject_session_id", return_value=None):
            await graph.ainvoke({"input": "x"}, config=None)

        assert orig_ainvoke.call_args.kwargs["config"] is None

    @pytest.mark.asyncio
    async def test_astream_injects_session_id(self) -> None:
        graph = _FakeGraph()
        apply_session_injection(graph)

        config = {"configurable": {"thread_id": "thread-456"}}
        with patch("migratowl.agent.session_graph.inject_session_id") as mock_inject:
            mock_inject.side_effect = lambda c: {
                **c,
                "metadata": {"langfuse_session_id": "thread-456"},
            }
            chunks = [chunk async for chunk in graph.astream({"input": "x"}, config=config)]

        mock_inject.assert_called_once_with(config)
        assert chunks == [{"chunk": 1}]

    @pytest.mark.asyncio
    async def test_astream_events_injects_session_id(self) -> None:
        graph = _FakeGraph()
        apply_session_injection(graph)

        config = {"configurable": {"thread_id": "thread-789"}}
        with patch("migratowl.agent.session_graph.inject_session_id") as mock_inject:
            mock_inject.side_effect = lambda c: {
                **c,
                "metadata": {"langfuse_session_id": "thread-789"},
            }
            events = [e async for e in graph.astream_events({"input": "x"}, config=config)]

        mock_inject.assert_called_once_with(config)
        assert events == [{"event": "on_chain_start", "data": {}}]

    def test_returns_same_graph_object(self) -> None:
        graph = _FakeGraph()
        result = apply_session_injection(graph)
        assert result is graph

    def test_preserves_other_attributes(self) -> None:
        graph = _FakeGraph()
        apply_session_injection(graph)
        assert graph.some_attr == "original"
