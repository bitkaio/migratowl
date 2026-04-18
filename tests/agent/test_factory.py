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

"""Tests for agent graph factory."""

from unittest.mock import MagicMock, patch

import pytest

from migratowl.agent.factory import create_migratowl_agent
from migratowl.config import Settings


def _make_mock_manager(mock_graph: MagicMock | None = None) -> MagicMock:
    """Return a mock KubernetesSandboxManager with sensible defaults."""
    from langchain_kubernetes import KubernetesSandboxManager

    mgr = MagicMock(spec=KubernetesSandboxManager)
    mgr._make_backend_factory.return_value = lambda _: MagicMock()
    mgr.create_agent.return_value = mock_graph or MagicMock()
    return mgr


class TestCreateMigratowlAgent:
    def test_returns_compiled_graph(self) -> None:
        mock_graph = MagicMock()
        mock_manager = _make_mock_manager(mock_graph)
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", return_value=mock_graph),
        ):
            graph = create_migratowl_agent(mock_manager, settings=settings)

        assert graph is mock_graph

    def test_makes_backend_factory_from_manager(self) -> None:
        mock_manager = _make_mock_manager()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        mock_manager._make_backend_factory.assert_called_once()

    def test_creates_expected_tool_count(self) -> None:
        mock_manager = _make_mock_manager()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        call_kwargs = mock_manager.create_agent.call_args[1]
        # 11 tools: clone, copy, detect, scan, check_outdated, update, validate, execute, changelog, read_manifest, patch_manifest
        assert len(call_kwargs["tools"]) == 11  # noqa: PLR2004

    def test_uses_init_chat_model_with_provider_and_name(self) -> None:
        mock_manager = _make_mock_manager()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        mock_init.assert_called_once()
        model_id = mock_init.call_args[0][0]
        assert model_id == f"{settings.model_provider}:{settings.model_name}"

    def test_applies_session_injection(self) -> None:
        raw_graph = MagicMock()
        patched_graph = MagicMock()
        mock_manager = _make_mock_manager(raw_graph)
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch(
                "migratowl.agent.factory.apply_session_injection", return_value=patched_graph
            ) as mock_inject,
        ):
            result = create_migratowl_agent(mock_manager, settings=settings)

        mock_inject.assert_called_once_with(raw_graph)
        assert result is patched_graph

    def test_passes_langfuse_handler_as_callback_when_configured(self) -> None:
        mock_manager = _make_mock_manager()
        settings = Settings(_env_file=None)
        mock_handler = MagicMock()

        with (
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory._langfuse_handler", mock_handler),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        call_kwargs = mock_init.call_args[1]
        assert call_kwargs["callbacks"] == [mock_handler]

    def test_no_langfuse_callback_when_not_configured(self) -> None:
        mock_manager = _make_mock_manager()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory._langfuse_handler", None),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        call_kwargs = mock_init.call_args[1]
        assert call_kwargs.get("callbacks") is None

    def test_passes_base_url_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_manager = _make_mock_manager()
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example.com")
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory._langfuse_handler", None),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        call_kwargs = mock_init.call_args[1]
        assert call_kwargs.get("base_url") == "https://proxy.example.com"

    def test_passes_response_format_to_manager_create_agent(self) -> None:
        from migratowl.models.schemas import ScanAnalysisReport

        mock_manager = _make_mock_manager()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        call_kwargs = mock_manager.create_agent.call_args[1]
        assert call_kwargs.get("response_format") is ScanAnalysisReport

    def test_system_prompt_directs_zero_confidence_packages_to_direct_report(self) -> None:
        """Packages with confidence=0 must be directly reported as non-breaking.

        They must NOT be delegated to the package-analyzer subagent: empirical
        evidence from the combined validate_project run already proves they don't
        break the build, so isolation testing is unnecessary and wastes sandbox capacity.
        """
        from migratowl.agent.factory import SYSTEM_PROMPT

        prompt = SYSTEM_PROMPT.format(confidence_threshold=0.7)
        assert "confidence = 0" in prompt or "confidence=0" in prompt

    def test_system_prompt_distinguishes_passing_vs_failing_combined_build(self) -> None:
        """When the combined build FAILS, packages not mentioned in errors were never
        compiled — their absence from the error output is not proof of safety.
        The prompt must distinguish the two cases so the model doesn't mark packages
        as confidence=0 simply because the failing build didn't reach them.
        """
        from migratowl.agent.factory import SYSTEM_PROMPT

        prompt = SYSTEM_PROMPT.format(confidence_threshold=0.7)
        assert "all" in prompt.lower() and "pass" in prompt.lower()
        assert (
            "not compiled" in prompt.lower()
            or "not reached" in prompt.lower()
            or "stopped" in prompt.lower()
        )

    def test_system_prompt_requires_sequential_subagent_dispatch(self) -> None:
        """Parallel subagent dispatches each call backend.execute() concurrently,
        overwhelming the sandbox.  The prompt must instruct sequential dispatch.
        """
        from migratowl.agent.factory import SYSTEM_PROMPT

        prompt = SYSTEM_PROMPT.format(confidence_threshold=0.7)
        assert "one at a time" in prompt or "sequentially" in prompt

    def test_system_prompt_substitutes_confidence_threshold(self) -> None:
        """SYSTEM_PROMPT.format(confidence_threshold=X) must render X in the output.

        The prompt contains two thresholds expressed as ``{confidence_threshold}``.
        If they are accidentally double-escaped as ``{{confidence_threshold}}``,
        the format call silently passes the argument without substituting it,
        and the agent's instructions will always contain the literal text
        ``{confidence_threshold}`` instead of the configured value.
        """
        from migratowl.agent.factory import SYSTEM_PROMPT

        prompt = SYSTEM_PROMPT.format(confidence_threshold=0.7)
        assert "0.7" in prompt, (
            "confidence_threshold was not substituted — check for double braces "
            "{{confidence_threshold}} in SYSTEM_PROMPT"
        )

    def test_no_base_url_when_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        mock_manager = _make_mock_manager()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory._langfuse_handler", None),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        call_kwargs = mock_init.call_args[1]
        assert "base_url" not in call_kwargs
