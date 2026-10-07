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

"""Monkey-patches for third-party library bugs.

Applied once at agent startup via ``apply_patches()``.
"""

from __future__ import annotations

import logging
import shlex
from typing import Any

logger = logging.getLogger(__name__)

def apply_patches() -> None:
    """Apply all monkey-patches. Idempotent — safe to call multiple times."""
    if getattr(apply_patches, "_applied", False):
        return
    _patch_grep_raw()
    _patch_filesystem_middleware_eviction()
    _patch_summarization_threshold()
    _patch_subagent_recursion_limit()
    _patch_sandbox_execute_errors()
    apply_patches._applied = True  # type: ignore[attr-defined]


def _patch_grep_raw() -> None:
    """Fix ``BaseSandbox.grep_raw`` crashing on malformed grep output lines.

    deepagents' parser does ``int(parts[1])`` without validation, which
    crashes on binary-file messages, shell artifacts (``2>/dev/null``),
    and evicted ``/large_tool_results/`` paths.  This patch wraps the
    ``int()`` call in a try/except so unparseable lines are skipped.

    Upstream issue: https://github.com/langchain-ai/deepagents/issues/TBD
    """
    from deepagents.backends.sandbox import BaseSandbox

    def grep_raw_patched(
        self: BaseSandbox,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> list | str:
        search_path = shlex.quote(path or ".")

        grep_opts = "-rHnF"

        glob_pattern = ""
        if glob:
            glob_pattern = f"--include='{glob}'"

        pattern_escaped = shlex.quote(pattern)

        cmd = f"grep {grep_opts} {glob_pattern} -e {pattern_escaped} {search_path} 2>/dev/null || true"
        result = self.execute(cmd)

        output = result.output.rstrip()
        if not output:
            return []

        matches: list[dict] = []
        for line in output.split("\n"):
            parts = line.split(":", 2)
            if len(parts) >= 3:  # noqa: PLR2004
                try:
                    line_no = int(parts[1])
                except ValueError:
                    continue
                matches.append(
                    {
                        "path": parts[0],
                        "line": line_no,
                        "text": parts[2],
                    }
                )

        return matches

    BaseSandbox.grep_raw = grep_raw_patched  # type: ignore[assignment]
    logger.info("Patched BaseSandbox.grep_raw to skip unparseable lines")


def _patch_filesystem_middleware_eviction() -> None:
    """Disable tool-result eviction in ``FilesystemMiddleware`` by default.

    deepagents evicts large tool results to ``/large_tool_results/`` and tells
    the agent to read them back piecemeal.  This creates read-file / grep loops
    that exhaust the recursion limit.  Eviction was designed for interactive
    chat sessions; for autonomous agents the summarization middleware already
    handles context pressure, so eviction is net-negative.

    We change the default of ``tool_token_limit_before_evict`` from ``20000``
    to ``None`` (disabled) while leaving explicit caller-supplied values intact.
    """
    from deepagents.middleware.filesystem import FilesystemMiddleware

    _original_init = FilesystemMiddleware.__init__

    def patched_init(self: FilesystemMiddleware, *args: Any, **kwargs: Any) -> None:
        if "tool_token_limit_before_evict" not in kwargs:
            kwargs["tool_token_limit_before_evict"] = None
        _original_init(self, *args, **kwargs)

    FilesystemMiddleware.__init__ = patched_init  # type: ignore[assignment]
    logger.info("Patched FilesystemMiddleware to disable eviction by default")


def _patch_summarization_threshold() -> None:
    """Compensate for ~4.6x token overcounting in count_tokens_approximately.

    deepagents' compute_summarization_defaults() uses ("fraction", 0.85) trigger
    which fires at ~37k real tokens for Claude Sonnet 4.6 (200k context). By
    replacing with ("tokens", 500_000), summarization fires at ~109k real tokens
    (500k / 4.6 overcounting factor), a reasonable 54% of context.
    """
    from deepagents.middleware import summarization as deepagents_summarization
    from deepagents.middleware.summarization import compute_summarization_defaults as _orig_compute

    def patched_compute_summarization_defaults(model: Any) -> Any:
        defaults = _orig_compute(model)
        if isinstance(defaults.get("trigger"), tuple) and defaults["trigger"][0] == "fraction":
            defaults["trigger"] = ("tokens", 500_000)
            tas = defaults.get("truncate_args_settings")
            if isinstance(tas, dict):
                tas_trigger = tas.get("trigger")
                if isinstance(tas_trigger, tuple) and tas_trigger[0] == "fraction":
                    tas["trigger"] = ("tokens", 500_000)
        return defaults

    deepagents_summarization.compute_summarization_defaults = patched_compute_summarization_defaults  # type: ignore[assignment]
    logger.info(
        "Patched compute_summarization_defaults to use token-based trigger (overcounting workaround)"
    )


def _patch_subagent_recursion_limit() -> None:
    """Inject recursion_limit=500 into subagent invocations.

    deepagents invokes subagents without binding a recursion_limit, so they
    run at LangGraph's default. We wrap each CompiledSubAgent runnable with
    .with_config({"recursion_limit": 500}) before it reaches the task tool;
    the bound config wins per-key merges (langgraph#7926), giving subagents
    5x headroom. Raw SubAgent specs (no "runnable" key, compiled internally
    by deepagents 0.6+) are passed through untouched.
    """
    from deepagents.middleware import subagents as _subagents_mod

    _orig_build_task_tool = _subagents_mod._build_task_tool

    def _patched_build_task_tool(subagents_list: Any, *args: Any, **kwargs: Any) -> Any:
        patched_specs = [
            {**spec, "runnable": spec["runnable"].with_config({"recursion_limit": 500})}
            if "runnable" in spec
            else spec
            for spec in subagents_list
        ]
        return _orig_build_task_tool(patched_specs, *args, **kwargs)  # type: ignore[arg-type]

    _subagents_mod._build_task_tool = _patched_build_task_tool  # type: ignore[assignment]
    logger.info("Patched _build_task_tool to inject recursion_limit=500 for subagents")


# RuntimeError text langchain-kubernetes raises when the sandbox-router returns
# 5xx — in practice the router's own 180s proxy timeout on a long command.
_GATEWAY_FAILURE_MARKER = "Failed to communicate with the sandbox"
_TIMEOUT_EXIT_CODE = 124  # same code coreutils `timeout` uses


def _patch_sandbox_execute_errors() -> None:
    """Return sandbox exec timeouts to the agent instead of raising.

    deepagents' ``execute`` tool only catches ``NotImplementedError`` and
    ``ValueError``, and Migratowl's own tools call ``backend.execute()``
    directly.  A ``TimeoutError`` from the raw-mode exec transport, or the
    gateway ``RuntimeError`` from the sandbox-router, therefore propagates
    out of the graph and fails the whole scan — e.g. one slow ``pytest``
    run.  This wraps ``KubernetesSandbox.execute``/``aexecute`` so those
    errors become a failed ``ExecuteResponse`` the agent can reason about.
    """
    from deepagents.backends.protocol import ExecuteResponse
    from langchain_kubernetes.sandbox import KubernetesSandbox

    def _as_failed_response(exc: Exception) -> ExecuteResponse | None:
        if isinstance(exc, TimeoutError) or (isinstance(exc, RuntimeError) and _GATEWAY_FAILURE_MARKER in str(exc)):
            return ExecuteResponse(
                output=(
                    f"Error: {exc} The command did not finish. Run a narrower "
                    "command (e.g. a subset of tests) or pass a larger timeout."
                ),
                exit_code=_TIMEOUT_EXIT_CODE,
                truncated=False,
            )
        return None

    original_execute = KubernetesSandbox.execute
    original_aexecute = KubernetesSandbox.aexecute

    def execute_patched(self: KubernetesSandbox, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        try:
            return original_execute(self, command, timeout=timeout)
        except (TimeoutError, RuntimeError) as exc:
            response = _as_failed_response(exc)
            if response is None:
                raise
            logger.warning("Sandbox exec failed, returned to agent: %s", exc)
            return response

    async def aexecute_patched(self: KubernetesSandbox, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        try:
            return await original_aexecute(self, command, timeout=timeout)
        except (TimeoutError, RuntimeError) as exc:
            response = _as_failed_response(exc)
            if response is None:
                raise
            logger.warning("Sandbox exec failed, returned to agent: %s", exc)
            return response

    KubernetesSandbox.execute = execute_patched  # type: ignore[method-assign]
    KubernetesSandbox.aexecute = aexecute_patched  # type: ignore[method-assign]
    logger.info("Patched KubernetesSandbox.execute/aexecute to return timeouts to the agent")
