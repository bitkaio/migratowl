"""Tests for subagent factories."""

from unittest.mock import MagicMock

import migratowl.agent.subagents as _subagents_mod


class TestCreatePackageAnalyzerSubagent:
    """create_package_analyzer_subagent returns a CompiledSubAgent with K8s backend wired through."""

    def _fake_graph(self):
        return MagicMock()

    def _call(self, monkeypatch, *, backend_factory=None, tools=None, model=None):
        monkeypatch.setattr(_subagents_mod, "create_deep_agent", lambda **kw: self._fake_graph())
        return _subagents_mod.create_package_analyzer_subagent(
            model=model or MagicMock(),
            backend_factory=backend_factory or MagicMock(),
            tools=tools if tools is not None else [],
        )

    def test_returns_dict_with_required_keys(self, monkeypatch) -> None:
        """CompiledSubAgent is a TypedDict — must have name, description, runnable."""
        result = self._call(monkeypatch)
        assert isinstance(result, dict)
        assert {"name", "description", "runnable"} <= result.keys()

    def test_name_is_package_analyzer(self, monkeypatch) -> None:
        """Name must match exactly what the main agent system prompt references via task()."""
        result = self._call(monkeypatch)
        assert result["name"] == "package-analyzer"

    def test_description_is_non_trivial(self, monkeypatch) -> None:
        """Description must be specific enough for the main agent to decide when to delegate."""
        result = self._call(monkeypatch)
        assert len(result["description"]) > 20

    def test_runnable_is_result_of_create_deep_agent(self, monkeypatch) -> None:
        """The runnable must be exactly what create_deep_agent returns."""
        fake_graph = MagicMock()
        monkeypatch.setattr(_subagents_mod, "create_deep_agent", lambda **kw: fake_graph)
        result = _subagents_mod.create_package_analyzer_subagent(
            model=MagicMock(), backend_factory=MagicMock(), tools=[]
        )
        assert result["runnable"] is fake_graph

    def test_backend_factory_wired_to_inner_graph(self, monkeypatch) -> None:
        """backend_factory must be passed as backend= to create_deep_agent.

        This is the core of the fix: the subagent gets a K8s backend so deepagents'
        built-in ls/read_file/grep/execute tools operate on the real sandbox filesystem.
        """
        captured: dict = {}
        monkeypatch.setattr(
            _subagents_mod,
            "create_deep_agent",
            lambda **kw: (captured.update(kw), MagicMock())[1],
        )
        backend_factory = MagicMock()
        _subagents_mod.create_package_analyzer_subagent(
            model=MagicMock(), backend_factory=backend_factory, tools=[]
        )
        assert captured.get("backend") is backend_factory

    def test_model_passed_to_inner_graph(self, monkeypatch) -> None:
        """model must be passed through so the subagent uses the shared rate-limited model."""
        captured: dict = {}
        monkeypatch.setattr(
            _subagents_mod,
            "create_deep_agent",
            lambda **kw: (captured.update(kw), MagicMock())[1],
        )
        model = MagicMock()
        _subagents_mod.create_package_analyzer_subagent(
            model=model, backend_factory=MagicMock(), tools=[]
        )
        assert captured.get("model") is model

    def test_tools_passed_to_inner_graph(self, monkeypatch) -> None:
        """tools list must be passed through so the subagent has its tool set."""
        captured: dict = {}
        monkeypatch.setattr(
            _subagents_mod,
            "create_deep_agent",
            lambda **kw: (captured.update(kw), MagicMock())[1],
        )
        tools = [MagicMock(), MagicMock()]
        _subagents_mod.create_package_analyzer_subagent(
            model=MagicMock(), backend_factory=MagicMock(), tools=tools
        )
        assert captured.get("tools") is tools
