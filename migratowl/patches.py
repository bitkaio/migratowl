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
    _patch_langchain_kubernetes_annotated()
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

    deepagents' _build_task_tool() calls subagent.invoke/ainvoke without
    a config argument, so subagents run at LangGraph's default recursion_limit
    of 100. We wrap each subagent runnable with .with_config({"recursion_limit": 500})
    before it is stored in the task tool's closure, giving subagents 5x headroom.
    """
    from deepagents.middleware import subagents as _subagents_mod

    _orig_build_task_tool = _subagents_mod._build_task_tool

    def _patched_build_task_tool(subagents_list, task_description=None):
        patched_specs = [
            {**spec, "runnable": spec["runnable"].with_config({"recursion_limit": 500})}
            for spec in subagents_list
        ]
        return _orig_build_task_tool(patched_specs, task_description)

    _subagents_mod._build_task_tool = _patched_build_task_tool
    logger.info("Patched _build_task_tool to inject recursion_limit=500 for subagents")


def _patch_langchain_kubernetes_annotated() -> None:
    """Inject missing symbols into langchain_kubernetes.manager module globals.

    manager.py (0.3.0.dev26) defines _AgentState inside create_agent() and
    imports Annotated, TypedDict, AnyMessage, and add_messages locally (not at
    module level), but from __future__ import annotations causes all annotations
    to be stored as strings.  When LangGraph calls
    get_type_hints(_AgentState, include_extras=True) during StateGraph
    construction, Python resolves strings against the class's *module* globals —
    not the enclosing method scope — so all four symbols must be present there.
    """
    from typing import Annotated

    import langchain_kubernetes.manager as _lk_manager
    from langchain_core.messages import AnyMessage
    from langgraph.graph.message import add_messages
    from typing_extensions import TypedDict

    needed = {
        "Annotated": Annotated,
        "AnyMessage": AnyMessage,
        "TypedDict": TypedDict,
        "add_messages": add_messages,
    }
    injected = []
    for name, val in needed.items():
        if not hasattr(_lk_manager, name):
            setattr(_lk_manager, name, val)
            injected.append(name)
    if injected:
        logger.info(
            "Patched langchain_kubernetes.manager: injected %s into module globals",
            ", ".join(injected),
        )
