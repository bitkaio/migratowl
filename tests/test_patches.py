# SPDX-License-Identifier: Apache-2.0

"""Tests for monkey-patches applied to deepagents."""

from deepagents.backends.sandbox import BaseSandbox
from deepagents.middleware.filesystem import FilesystemMiddleware

from migratowl.patches import apply_patches


class TestGrepRawPatch:
    """Verify that the patched grep_raw skips unparseable lines instead of crashing."""

    def test_valid_grep_output(self) -> None:
        apply_patches()
        # Simulate a grep_raw call with well-formed output
        # We test the parsing logic directly by calling the patched method
        # on a mock that returns controlled output.
        from unittest.mock import MagicMock

        backend = MagicMock(spec=BaseSandbox)
        backend.execute.return_value = MagicMock(
            output="src/main.py:10:import os\nsrc/main.py:20:import sys\n"
        )
        # Call the patched method unbound
        result = BaseSandbox.grep_raw(backend, "import")
        assert len(result) == 2
        assert result[0] == {"path": "src/main.py", "line": 10, "text": "import os"}
        assert result[1] == {"path": "src/main.py", "line": 20, "text": "import sys"}

    def test_skips_line_with_non_numeric_part(self) -> None:
        """The exact crash case: parts[1] is ' /large_tool_results' or ' 2>/dev/null'."""
        apply_patches()
        from unittest.mock import MagicMock

        backend = MagicMock(spec=BaseSandbox)
        backend.execute.return_value = MagicMock(
            output=(
                "src/main.py:10:import os\n"
                "Binary file /large_tool_results/abc matches\n"
                "grep: 2>/dev/null: No such file or directory\n"
                "src/util.py:5:import json\n"
            )
        )
        result = BaseSandbox.grep_raw(backend, "import")
        # Should get the 2 valid lines, skipping the malformed ones
        assert len(result) == 2
        assert result[0]["path"] == "src/main.py"
        assert result[1]["path"] == "src/util.py"

    def test_empty_output(self) -> None:
        apply_patches()
        from unittest.mock import MagicMock

        backend = MagicMock(spec=BaseSandbox)
        backend.execute.return_value = MagicMock(output="")
        result = BaseSandbox.grep_raw(backend, "nothing")
        assert result == []

    def test_all_malformed_lines(self) -> None:
        apply_patches()
        from unittest.mock import MagicMock

        backend = MagicMock(spec=BaseSandbox)
        backend.execute.return_value = MagicMock(
            output="garbage line\nanother: bad: line\n"
        )
        result = BaseSandbox.grep_raw(backend, "pattern")
        assert result == []

    def test_idempotent(self) -> None:
        """Calling apply_patches multiple times should not break anything."""
        apply_patches()
        apply_patches()
        from unittest.mock import MagicMock

        backend = MagicMock(spec=BaseSandbox)
        backend.execute.return_value = MagicMock(output="a.py:1:hello\n")
        result = BaseSandbox.grep_raw(backend, "hello")
        assert len(result) == 1


class TestSummarizationPatch:
    """Verify that compute_summarization_defaults is patched to use token-based trigger."""

    def test_compute_summarization_defaults_uses_token_trigger_not_fraction(self) -> None:
        """After apply_patches(), trigger should be ('tokens', ...) not ('fraction', ...)."""
        from unittest.mock import MagicMock

        from deepagents.middleware.summarization import compute_summarization_defaults

        apply_patches()

        mock_model = MagicMock()
        mock_model.profile = {"max_input_tokens": 200000}

        defaults = compute_summarization_defaults(mock_model)
        trigger = defaults["trigger"]
        assert isinstance(trigger, tuple), f"Expected tuple, got {type(trigger)}"
        assert trigger[0] == "tokens", f"Expected 'tokens' trigger, got {trigger[0]!r}"

    def test_truncate_args_settings_also_patched(self) -> None:
        """truncate_args_settings['trigger'] should also be token-based after patching."""
        from unittest.mock import MagicMock

        from deepagents.middleware.summarization import compute_summarization_defaults

        apply_patches()

        mock_model = MagicMock()
        mock_model.profile = {"max_input_tokens": 200000}

        defaults = compute_summarization_defaults(mock_model)
        tas = defaults.get("truncate_args_settings")
        assert isinstance(tas, dict), f"Expected dict for truncate_args_settings, got {type(tas)}"
        trigger = tas["trigger"]
        assert isinstance(trigger, tuple), f"Expected tuple, got {type(trigger)}"
        assert trigger[0] == "tokens", f"Expected 'tokens' trigger in truncate_args_settings, got {trigger[0]!r}"

    def test_patch_idempotent_for_summarization(self) -> None:
        """Calling apply_patches() twice still gives correct token-based trigger."""
        from unittest.mock import MagicMock

        from deepagents.middleware.summarization import compute_summarization_defaults

        apply_patches()
        apply_patches()

        mock_model = MagicMock()
        mock_model.profile = {"max_input_tokens": 200000}

        defaults = compute_summarization_defaults(mock_model)
        assert defaults["trigger"][0] == "tokens"


class TestFilesystemMiddlewareEvictionPatch:
    """Verify that FilesystemMiddleware is patched to disable eviction by default."""

    def test_new_instance_has_eviction_disabled(self) -> None:
        """After apply_patches(), new FilesystemMiddleware instances disable eviction."""
        apply_patches()
        mw = FilesystemMiddleware(backend=None)  # type: ignore[arg-type]
        assert mw._tool_token_limit_before_evict is None

    def test_explicit_limit_still_works(self) -> None:
        """Callers that pass an explicit limit should still have it respected."""
        apply_patches()
        mw = FilesystemMiddleware(backend=None, tool_token_limit_before_evict=5000)  # type: ignore[arg-type]
        assert mw._tool_token_limit_before_evict == 5000

    def test_patch_idempotent_for_eviction(self) -> None:
        apply_patches()
        apply_patches()
        mw = FilesystemMiddleware(backend=None)  # type: ignore[arg-type]
        assert mw._tool_token_limit_before_evict is None


class TestSubagentRecursionLimitPatch:
    """Verify that _build_task_tool is patched to inject recursion_limit=500."""

    def test_build_task_tool_calls_with_config_on_subagent_runnable(self) -> None:
        """After apply_patches(), _build_task_tool wraps each runnable with recursion_limit=500."""
        from unittest.mock import MagicMock

        apply_patches()
        from deepagents.middleware import subagents as _subagents_mod

        mock_runnable = MagicMock()
        wrapped = MagicMock()
        mock_runnable.with_config.return_value = wrapped

        specs = [{"name": "test-agent", "description": "test agent", "runnable": mock_runnable}]
        _subagents_mod._build_task_tool(specs)

        mock_runnable.with_config.assert_called_once_with({"recursion_limit": 500})

    def test_build_task_tool_forwards_keyword_only_args(self) -> None:
        """deepagents 0.6 calls _build_task_tool with keyword-only args
        (private_state_keys, state_schema); the patch must forward them."""
        from unittest.mock import MagicMock

        apply_patches()
        from deepagents.middleware import subagents as _subagents_mod

        mock_runnable = MagicMock()
        wrapped = MagicMock()
        mock_runnable.with_config.return_value = wrapped

        specs = [{"name": "kwargs-agent", "description": "test", "runnable": mock_runnable}]
        _subagents_mod._build_task_tool(
            specs,
            private_state_keys=frozenset({"secret"}),
            state_schema=None,
        )

        mock_runnable.with_config.assert_called_once_with({"recursion_limit": 500})

    def test_build_task_tool_skips_raw_specs_without_runnable(self) -> None:
        """deepagents 0.6 mixes raw SubAgent specs (no 'runnable' key) with
        CompiledSubAgent specs; the patch must pass raw specs through untouched
        instead of raising KeyError."""
        from unittest.mock import MagicMock

        apply_patches()
        from deepagents.middleware import subagents as _subagents_mod

        mock_runnable = MagicMock()
        mock_runnable.with_config.return_value = MagicMock()

        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

        raw_spec = {
            "name": "raw-agent",
            "description": "raw spec compiled internally by deepagents",
            "system_prompt": "You are a test agent.",
            "tools": [],
            "model": GenericFakeChatModel(messages=iter([])),
        }
        compiled_spec = {"name": "compiled-agent", "description": "test", "runnable": mock_runnable}
        _subagents_mod._build_task_tool([raw_spec, compiled_spec])

        mock_runnable.with_config.assert_called_once_with({"recursion_limit": 500})

    def test_recursion_limit_patch_idempotent(self) -> None:
        """Double apply_patches() still results in with_config called exactly once per _build_task_tool call."""
        from unittest.mock import MagicMock

        apply_patches()
        apply_patches()
        from deepagents.middleware import subagents as _subagents_mod

        mock_runnable = MagicMock()
        wrapped = MagicMock()
        mock_runnable.with_config.return_value = wrapped

        specs = [{"name": "idempotent-agent", "description": "test", "runnable": mock_runnable}]
        _subagents_mod._build_task_tool(specs)

        mock_runnable.with_config.assert_called_once_with({"recursion_limit": 500})


class TestVestigialKubernetesPatchRemoved:
    """The langchain_kubernetes.manager annotation patch is vestigial.

    langchain-kubernetes 0.4.0's manager.py no longer defines ``_AgentState``
    inside ``create_agent()``, so the ``get_type_hints`` resolution quirk the
    patch worked around no longer exists. The patch must be removed to avoid
    reaching into library internals that are no longer relevant.
    """

    def test_patch_function_removed(self) -> None:
        import migratowl.patches as patches_mod

        assert not hasattr(patches_mod, "_patch_langchain_kubernetes_annotated")


class _RaisingBackend:
    """Minimal KubernetesBackendProtocol stand-in whose execute() raises."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.id = "fake-sandbox"

    def execute(self, command: str, *, timeout: int | None = None):
        raise self._exc

    async def aexecute(self, command: str, *, timeout: int | None = None):
        raise self._exc


class TestSandboxExecuteErrorPatch:
    """A slow or unreachable command must come back to the agent as a failed
    ExecuteResponse instead of an exception that kills the whole scan."""

    RAW_TIMEOUT = TimeoutError("Command timed out after 120s in pod 'deepagents-47a1fb55': 'cd /w && pytest -x'")
    GATEWAY_FAILURE = RuntimeError(
        "Failed to communicate with the sandbox via the gateway at http://127.0.0.1:56825/execute."
    )

    def _sandbox(self, exc: Exception):
        from langchain_kubernetes.sandbox import KubernetesSandbox

        apply_patches()
        return KubernetesSandbox(backend=_RaisingBackend(exc))  # type: ignore[arg-type]

    def test_timeout_returns_failed_response(self) -> None:
        result = self._sandbox(self.RAW_TIMEOUT).execute("pytest -x", timeout=120)
        assert result.exit_code == 124
        assert "timed out after 120s" in result.output

    async def test_async_timeout_returns_failed_response(self) -> None:
        result = await self._sandbox(self.RAW_TIMEOUT).aexecute("pytest -x", timeout=120)
        assert result.exit_code == 124
        assert "timed out after 120s" in result.output

    def test_gateway_failure_returns_failed_response(self) -> None:
        result = self._sandbox(self.GATEWAY_FAILURE).execute("pytest -x")
        assert result.exit_code == 124
        assert "Failed to communicate with the sandbox" in result.output

    def test_unrelated_runtime_error_still_raises(self) -> None:
        import pytest

        sandbox = self._sandbox(RuntimeError("no thread_id in LangGraph config"))
        with pytest.raises(RuntimeError, match="no thread_id"):
            sandbox.execute("ls")
