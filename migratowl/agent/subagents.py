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

"""Subagent factories for the Migratowl agent."""

from collections.abc import Callable, Sequence
from typing import Any

from deepagents import CompiledSubAgent, create_deep_agent

PACKAGE_ANALYZER_PROMPT = """\
You are a dependency migration analyzer for a single package.

You are given a package name, its current_version and latest_version, and its
ecosystem. Test that one upgrade in isolation, in its own folder.

Workflow:
1. Copy the source to the package folder with copy_source("{package_name}").
2. Update ONLY this package with update_dependencies("{package_name}", ecosystem, packages_json).
3. Build and test with validate_project("{package_name}", ecosystem). Use execute_project
   only for a custom command validate_project does not cover.
4. If validation fails:
   - Read the failing output; use read_manifest(path=<absolute path>) to look at a manifest.
   - Call fetch_changelog_tool to understand what changed.
   - Report is_breaking=true and suggest a fix citing the exact changelog section.
5. If validation passes:
   - No major-version bump (current_major == latest_major): report is_breaking=false \
with confidence=1.0. Do NOT call fetch_changelog_tool.
   - Major-version bump (current_major < latest_major): call fetch_changelog_tool, \
inspect for breaking changes, and set is_breaking, changelog_citation, and \
suggested_human_fix accordingly. Use confidence=0.9.

Rules:
- Do not use ls, read_file, write_file, edit_file, glob, grep, execute or write_todos;
  they do not work in this sandbox. Use the tools named above.
- Never call the same tool twice with the same arguments.
- Never invent a changelog citation; use "" when you have nothing concrete.

Your final message must contain ONLY this JSON object (no prose, no markdown wrapper).
Use "" for any text field you have nothing for:
{"dependency_name": "...", "is_breaking": true|false, "error_summary": "...", \
"changelog_citation": "...", "suggested_human_fix": "...", "confidence": 0.0-1.0}
"""


def create_package_analyzer_subagent(
    model: Any,
    backend_factory: Callable,
    tools: list,
    middleware: Sequence[Any] = (),
) -> CompiledSubAgent:
    """Create the package-analyzer CompiledSubAgent with K8s backend.

    Uses CompiledSubAgent (not a dict spec) so the inner graph gets the same
    Kubernetes sandbox backend as the main agent — giving the subagent access
    to deepagents' built-in ls, read_file, grep, and execute tools against the
    real K8s filesystem rather than the default in-memory StateBackend.
    """
    graph = create_deep_agent(
        model=model,
        system_prompt=PACKAGE_ANALYZER_PROMPT,
        tools=tools,
        backend=backend_factory,
        middleware=list(middleware),
    )
    return CompiledSubAgent(
        name="package-analyzer",
        description=(
            "Analyzes a single package upgrade in isolation. Copies source, "
            "updates only that package, runs tests, optionally fetches changelog, "
            "and returns an AnalysisReport JSON object."
        ),
        runnable=graph,
    )