# SPDX-License-Identifier: Apache-2.0

"""Tests for the agent graph factory (migratowl.agent.graph)."""

import importlib
import sys
from unittest.mock import MagicMock, patch


class TestBuildGraphFactory:
    """build_graph() wires patches + sandbox manager + agent, with no import-time side effects."""

    def test_no_manager_created_at_import(self) -> None:
        """Importing the module must NOT provision a sandbox manager (regression guard
        for the old import-time singleton)."""
        sys.modules.pop("migratowl.agent.graph", None)
        with patch("migratowl.agent.sandbox.create_sandbox_manager") as mock_create:
            import migratowl.agent.graph  # noqa: F401

            importlib.reload(migratowl.agent.graph)
            mock_create.assert_not_called()

    def test_build_graph_applies_patches_and_wires_manager(self) -> None:
        sys.modules.pop("migratowl.agent.graph", None)
        import migratowl.agent.graph as graph_mod

        importlib.reload(graph_mod)

        fake_manager = MagicMock()
        fake_graph = MagicMock()
        with (
            patch.object(graph_mod, "apply_patches") as mock_patches,
            patch.object(graph_mod, "create_sandbox_manager", return_value=fake_manager) as mock_create,
            patch.object(graph_mod, "create_migratowl_agent", return_value=fake_graph) as mock_agent,
        ):
            result = graph_mod.build_graph()

            mock_patches.assert_called_once()
            mock_create.assert_called_once()
            mock_agent.assert_called_once()
            assert result is fake_graph

    def test_graph_is_zero_arg_callable_factory(self) -> None:
        """langgraph.json points at `graph` — it must be a zero-arg callable
        (langgraph-cli invokes a factory with no args)."""
        import inspect

        sys.modules.pop("migratowl.agent.graph", None)
        import migratowl.agent.graph as graph_mod

        importlib.reload(graph_mod)

        assert callable(graph_mod.graph)
        sig = inspect.signature(graph_mod.graph)
        assert len(sig.parameters) == 0
