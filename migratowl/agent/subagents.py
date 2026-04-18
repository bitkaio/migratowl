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

from collections.abc import Callable
from typing import Any

from deepagents import CompiledSubAgent, create_deep_agent

PACKAGE_ANALYZER_PROMPT = """\
You are a dependency migration analyzer for a single package.

You are given a package name, its current and latest version, the ecosystem,
and install/test commands.

You have access to the Kubernetes sandbox filesystem via built-in tools:
- ls, read_file, grep: inspect the copied repository and error output
- execute: run ad-hoc shell commands (check imports, verify installed versions, etc.)

Workflow:
1. Copy source to the package folder using copy_source("{package_name}").
2. Update ONLY the specified package using update_dependencies("{package_name}", ecosystem, packages_json).
3. Run the project using execute_project.
4. If tests fail or produce warnings:
   - Use ls, read_file, grep, or execute to inspect files and error details.
   - Call fetch_changelog_tool to understand what changed.
   - Suggest a fix citing the exact changelog section.
5. If tests pass cleanly, report is_breaking=false with high confidence.
   Do NOT call fetch_changelog_tool if there are no errors.

Your final message must contain ONLY this JSON object (no prose, no markdown wrapper):
{"dependency_name": "...", "is_breaking": true|false, "error_summary": "...|null", \
"changelog_citation": "...|null", "suggested_human_fix": "...|null", "confidence": 0.0-1.0}
"""


def create_package_analyzer_subagent(
    model: Any,
    backend_factory: Callable,
    tools: list,
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
