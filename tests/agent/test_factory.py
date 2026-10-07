# SPDX-License-Identifier: Apache-2.0

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

    def test_uses_init_chat_model_with_provider_and_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock_manager = _make_mock_manager()
        monkeypatch.delenv("MIGRATOWL_MODEL_PROVIDER", raising=False)
        monkeypatch.delenv("MIGRATOWL_MODEL_ALIAS", raising=False)
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
        monkeypatch.delenv("MIGRATOWL_MODEL_PROVIDER", raising=False)
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        monkeypatch.delenv("MIGRATOWL_LITELLM_BASE_URL", raising=False)
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
        monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
        monkeypatch.delenv("MIGRATOWL_LITELLM_BASE_URL", raising=False)
        monkeypatch.delenv("MIGRATOWL_MODEL_PROVIDER", raising=False)
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

    def test_litellm_provider_uses_openai_sdk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LiteLLM is OpenAI-compatible, so it should use the openai SDK."""
        mock_manager = _make_mock_manager()
        monkeypatch.setenv("MIGRATOWL_MODEL_PROVIDER", "litellm")
        monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:6655/litellm/v1")
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory._langfuse_handler", None),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        model_id = mock_init.call_args[0][0]
        # Should use openai SDK, not "litellm:" prefix
        assert model_id.startswith("openai:")

    def test_litellm_provider_uses_litellm_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """LiteLLM provider should use litellm_base_url setting."""
        mock_manager = _make_mock_manager()
        monkeypatch.setenv("MIGRATOWL_MODEL_PROVIDER", "litellm")
        monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:6655/litellm/v1")
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory._langfuse_handler", None),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        call_kwargs = mock_init.call_args[1]
        assert call_kwargs.get("base_url") == "http://localhost:6655/litellm/v1"

    def test_model_alias_overrides_model_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """model_alias should be used instead of model_name when set."""
        mock_manager = _make_mock_manager()
        monkeypatch.setenv("MIGRATOWL_MODEL_ALIAS", "anthropic--claude-sonnet-latest")
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory._langfuse_handler", None),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        model_id = mock_init.call_args[0][0]
        assert "anthropic--claude-sonnet-latest" in model_id
        assert "claude-sonnet-5" not in model_id

    def test_model_alias_with_litellm_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """model_alias should work with litellm provider."""
        mock_manager = _make_mock_manager()
        monkeypatch.setenv("MIGRATOWL_MODEL_PROVIDER", "litellm")
        monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:6655/litellm/v1")
        monkeypatch.setenv("MIGRATOWL_MODEL_ALIAS", "anthropic--claude-sonnet-latest")
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory._langfuse_handler", None),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        model_id = mock_init.call_args[0][0]
        assert model_id == "openai:anthropic--claude-sonnet-latest"


class TestSystemPromptMajorVersionChangelog:
    def test_fetches_changelog_for_major_bump_when_tests_pass(self) -> None:
        from migratowl.agent.factory import SYSTEM_PROMPT

        prompt = SYSTEM_PROMPT.format(confidence_threshold=0.7)
        assert "major" in prompt.lower()
        assert "fetch_changelog" in prompt or "changelog" in prompt.lower()
        assert "0.9" in prompt

    def test_no_major_bump_keeps_1_0_confidence(self) -> None:
        from migratowl.agent.factory import SYSTEM_PROMPT

        prompt = SYSTEM_PROMPT.format(confidence_threshold=0.7)
        assert "1.0" in prompt

    def test_fetch_changelog_rule_includes_major_bump_exception(self) -> None:
        from migratowl.agent.factory import SYSTEM_PROMPT

        prompt = SYSTEM_PROMPT.format(confidence_threshold=0.7)
        important_rules_section = prompt.split("## Important Rules")[1]
        assert "major" in important_rules_section.lower()


class TestCheckpointerAndRateLimiter:
    """Phase 0d + Phase 4: injected rate_limiter and checkpointer."""

    def test_checkpointer_defaults_none(self) -> None:
        mock_manager = _make_mock_manager()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        call_kwargs = mock_manager.create_agent.call_args[1]
        assert call_kwargs.get("checkpointer") is None

    def test_forwards_checkpointer_to_manager_create_agent(self) -> None:
        mock_manager = _make_mock_manager()
        settings = Settings(_env_file=None)
        sentinel = object()

        with (
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
        ):
            create_migratowl_agent(mock_manager, settings=settings, checkpointer=sentinel)

        call_kwargs = mock_manager.create_agent.call_args[1]
        assert call_kwargs.get("checkpointer") is sentinel

    def test_uses_injected_rate_limiter(self) -> None:
        """An injected rate_limiter is passed to init_chat_model instead of a per-scan one."""
        mock_manager = _make_mock_manager()
        settings = Settings(_env_file=None)
        injected = MagicMock()

        with (
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
        ):
            create_migratowl_agent(mock_manager, settings=settings, rate_limiter=injected)

        assert mock_init.call_args[1]["rate_limiter"] is injected

    def test_builds_default_rate_limiter_when_not_injected(self) -> None:
        """Backwards compat: a rate_limiter is still built if none is injected."""
        mock_manager = _make_mock_manager()
        settings = Settings(_env_file=None)

        with (
            patch("migratowl.agent.factory.init_chat_model") as mock_init,
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
        ):
            create_migratowl_agent(mock_manager, settings=settings)

        assert mock_init.call_args[1].get("rate_limiter") is not None


class TestOnSandboxAcquired:
    """Phase 5: the get_sandbox closure fires on_sandbox_acquired with the sandbox id."""

    def _build_with_callback(self, callback):
        """Build the agent and return the wrapped get_sandbox closure passed to tools."""
        sandbox = MagicMock()
        sandbox.id = "sbx-99"

        mock_manager = _make_mock_manager()
        mock_manager._make_backend_factory.return_value = lambda _: sandbox
        settings = Settings(_env_file=None)

        captured = {}

        def fake_clone_tool(get_sandbox, **kwargs):
            captured["get_sandbox"] = get_sandbox
            return MagicMock()

        with (
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection", side_effect=lambda g: g),
            patch("migratowl.agent.factory.create_clone_repo_tool", side_effect=fake_clone_tool),
        ):
            create_migratowl_agent(
                mock_manager, settings=settings, on_sandbox_acquired=callback
            )
        return captured["get_sandbox"], sandbox

    def test_callback_fires_once_with_sandbox_id(self) -> None:
        seen: list[str] = []
        get_sandbox, sandbox = self._build_with_callback(lambda sid: seen.append(sid))

        assert get_sandbox() is sandbox
        assert get_sandbox() is sandbox
        assert seen == ["sbx-99"]

    def test_callback_fires_from_non_loop_thread(self) -> None:
        """Tools run get_sandbox on a worker thread; the callback must work there
        (regression guard against asyncio.create_task, which needs a running loop)."""
        import threading

        seen: list[str] = []
        get_sandbox, _ = self._build_with_callback(lambda sid: seen.append(sid))

        t = threading.Thread(target=get_sandbox)
        t.start()
        t.join()
        assert seen == ["sbx-99"]

    def test_no_callback_is_safe(self) -> None:
        get_sandbox, sandbox = self._build_with_callback(None)
        assert get_sandbox() is sandbox


class TestBuildTools:
    def test_returns_every_tool_by_name(self) -> None:
        from migratowl.agent.factory import build_tools

        tools = build_tools(_make_mock_manager(), settings=Settings(_env_file=None))

        assert tools.clone_repo.name == "clone_repo"
        assert tools.scan_dependencies.name == "scan_dependencies"
        assert tools.check_outdated_deps.name == "check_outdated_deps"
        assert tools.copy_source.name == "copy_source"
        assert tools.update_dependencies.name == "update_dependencies"
        assert tools.validate_project.name == "validate_project"
        assert tools.fetch_changelog.name == "fetch_changelog_tool"
        assert tools.read_manifest.name == "read_manifest"

    def test_on_sandbox_acquired_fires_once(self) -> None:
        from migratowl.agent.factory import build_tools

        sandbox = MagicMock(id="sbx-1")
        sandbox.execute.return_value = MagicMock(exit_code=0, output="file")
        mgr = _make_mock_manager()
        mgr._make_backend_factory.return_value = lambda _: sandbox
        seen: list[str] = []

        tools = build_tools(mgr, settings=Settings(_env_file=None), on_sandbox_acquired=seen.append)
        tools.copy_source.invoke({"folder_name": "main"})
        tools.copy_source.invoke({"folder_name": "other"})

        assert seen == ["sbx-1"]

    def test_create_agent_reuses_given_tools(self) -> None:
        from migratowl.agent.factory import build_tools

        mgr = _make_mock_manager()
        settings = Settings(_env_file=None)
        tools = build_tools(mgr, settings=settings)

        with (
            patch("migratowl.agent.factory.init_chat_model"),
            patch("migratowl.agent.factory.create_package_analyzer_subagent"),
            patch("migratowl.agent.factory.apply_session_injection"),
            patch("migratowl.agent.factory.build_tools") as mock_build,
        ):
            create_migratowl_agent(mgr, settings=settings, tools=tools)

        mock_build.assert_not_called()
        passed = mgr.create_agent.call_args.kwargs["tools"]
        assert tools.clone_repo in passed
