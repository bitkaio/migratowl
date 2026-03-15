# MigratOwl — Roadmap

Ordered by implementation priority. Each task builds on the previous ones.

1. **Base deepagent init** — Set up the deepagents agent with Claude Sonnet, system prompt, and LangGraph graph export. ✅

2. **Kubernetes sandbox init** — Initialize langchain-kubernetes provider at startup, create sandbox via agent-sandbox mode, wire as backend factory for deepagents. ✅

3. **Repo clone tool** — Agent tool that clones a given repository (by URL + branch) into the Kubernetes sandbox workspace so the agent can operate on real project files.

4. **Configuration system** — Centralized config via pydantic-settings with typed env vars (`MIGRATOWL_` prefix) for API keys, sandbox settings, and pipeline parameters.

5. **Data models & schemas** — Pydantic models for all pipeline data: ScanWebhookPayload, Dependency, OutdatedDependency, ScanResult, ExecutionResult, AnalysisReport, ScanAnalysisReport.

6. **Language detection** — Detect project language inside the sandbox by checking for marker files (pyproject.toml, package.json, go.mod, Cargo.toml) and select the appropriate build/test commands.

7. **Dependency scanning** — Parse manifest files (requirements.txt, pyproject.toml, package.json, go.mod, Cargo.toml) to extract declared dependencies and their current versions.

8. **Package registry queries** — Query PyPI, npm, crates.io, and proxy.golang.org to determine latest available versions and enrich with metadata (homepage, repository, changelog URLs).

9. **Dependency bumping** — Upgrade a single dependency to its latest version inside the sandbox using the appropriate package manager (pip, npm, go get, cargo).

10. **Changelog fetching tool** — Agent tool that fetches changelogs using essential strategies: explicit URL from registry metadata, GitHub file lookup (Trees API or raw CDN), and GitHub Releases via GraphQL. Includes version_extract() to trim to relevant section.

11. **Agent investigation orchestrator** — Wire the deepagents agent for Phase 2: hand it a prepared sandbox with bumped dependency, provide sandbox tools + changelog tool, and extract a structured AnalysisReport.

12. **Pipeline orchestrator** — Coordinate the full pipeline: Phase 0 (scan) → fan-out per dependency → Phase 1 (prep) → Phase 2 (investigate) → Phase 3 (cleanup) → aggregate into ScanAnalysisReport.

13. **FastAPI webhook endpoint** — POST /webhook accepting ScanWebhookPayload, returning 202 Accepted, and dispatching the pipeline as a background asyncio task.
