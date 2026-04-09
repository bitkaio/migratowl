# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **GitLab support** — `git_provider: "gitlab"` is now a valid value on `POST /webhook`.
  Migratowl posts a note (comment) on the merge request and sets GitLab commit statuses
  (`running` → `success` / `failed`) using the GitLab REST API v4 (`migratowl/git/gitlab.py`).
  Self-hosted instances are supported via `GITLAB_API_URL`.
- **`commit_sha` field on `POST /webhook`** — when provided alongside `pr_number`, Migratowl
  posts a `pending` / `running` commit status at scan start and updates it to `success` or
  `failure` / `error` when the scan completes or fails.
- **PR/MR notification system** (`migratowl/git/notify.py`) — three lifecycle hooks wired into
  the scan background task: `notify_pr_start` (pending status), `notify_pr_done` (comment +
  final status), `notify_pr_failed` (error status). All failures are logged and swallowed so a
  notification error never aborts a scan.
- **PR comment formatter** (`migratowl/git/formatter.py`) — renders a `ScanAnalysisReport` as
  a markdown table (breaking packages first, safe packages after) with confidence percentages and
  fix suggestions; includes a collapsible `<details>` block for skipped packages and a scan
  duration footer.
- **`GitHubClient`** (`migratowl/git/github.py`) — thin async wrapper around the GitHub REST API
  (`POST /repos/{owner}/{repo}/issues/{pr}/comments`, `POST .../statuses/{sha}`); supports
  GitHub Enterprise Server via `GITHUB_API_URL` / `github_api_url`.
- **GitHub Actions example workflow** (`docs/examples/dependabot-scan.yml`) — drop-in workflow
  that triggers Migratowl on every Dependabot PR; fires on `pull_request` events, posts the repo
  URL, branch, PR number, and commit SHA to the webhook endpoint.
- **`git_provider` validated as a literal** — `ScanWebhookPayload.git_provider` is now typed
  `Literal["github", "gitlab"]` (previously an unvalidated `str`), so invalid values are rejected
  at webhook ingestion time.
- **`GITLAB_TOKEN` / `GITLAB_API_URL` config** — new settings read from standard env vars
  (`GITLAB_TOKEN`, `GITLAB_API_URL`) with `MIGRATOWL_` prefix aliases.
- **`GITHUB_API_URL` config** — new setting (default `https://api.github.com`) to support GitHub
  Enterprise Server without code changes.
- **`mode` field on `POST /webhook`** — controls how the latest available version is resolved
  when checking for outdated dependencies. `"safe"` (default) respects the declared semver
  constraint (e.g. `^4.21.2` only flags a newer version if one exists within `>=4.21.2,<5.0.0`).
  `"normal"` ignores the operator and compares the bare version against the globally highest
  published version, surfacing major-version bumps such as `express 4.x → 5.x` that the registry's
  own `latest` tag would otherwise hide.
- **`include_prerelease` field on `POST /webhook`** — when `true`, pre-release versions
  (alpha, beta, RC, dev) are included when determining the latest available version. Defaults to
  `false`; orthogonal to `mode` and can be combined with either value.
- **Per-ecosystem all-versions registry queries** — `check_outdated_deps` now fetches the full
  published version list from each registry rather than relying on a single "latest" pointer:
  - **npm**: reads `versions` object keys from the packument (already fetched, no extra call)
  - **PyPI**: reads `releases` dict keys from the package JSON (already fetched, no extra call)
  - **crates.io**: new call to `/api/v1/crates/{name}/versions`; yanked versions are excluded
  - **Go module proxy**: switched from `/@latest` (JSON) to `/@v/list` (newline-separated text)
    to obtain the full version history
  - **Maven Central**: query extended with `core=gav&rows=100` to retrieve all published versions
    instead of the single `latestVersion` field
- **`_constraint_to_specifier()` helper** — parses npm/Cargo `^`/`~` operators and Python-style
  range specifiers into a `packaging.specifiers.SpecifierSet` for constraint-aware filtering;
  handles the `0.x` and `0.0.x` special cases of caret semantics
- **`_max_version()` helper** — picks the highest version from a list with optional pre-release
  filtering using `packaging.version.Version.is_prerelease`
- **`CheckOptions` dataclass** — internal configuration object carrying `mode` and
  `include_prerelease`; threaded from the webhook payload through the agent factory and tool
  factory to every per-ecosystem registry query function

### Fixed

- Standardized casing of "Migratowl" (previously inconsistently written as "MigratOwl") across
  all source files, documentation, and comments

## [0.1.0] - 2026-04-01

Initial release.

### Added

- **LangGraph agent** — stateful migration agent built on deepagents and LangGraph, exposed via
  `langgraph.json` for compatibility with deep-agents-ui; Kubernetes sandbox created lazily on
  first invocation with thread-safe double-checked locking and `atexit` cleanup
- **Langfuse session injection** — `session_graph.py` patches `ainvoke`, `astream`, and
  `astream_events` in-place to map LangGraph Server `thread_id` to Langfuse `langfuse_session_id`
  without breaking `isinstance(graph, Pregel)` validation
- **FastAPI webhook** — `POST /scan` endpoint accepts `ScanWebhookPayload`, creates a background
  job, and returns a job ID immediately; job status queryable via `GET /jobs/{id}`;
  single-concurrent-scan semaphore prevents resource exhaustion
- **Language detection tool** — identifies the ecosystem (Python, Go, Java, Node.js) from manifest
  files present in the cloned repository inside the K8s sandbox
- **Dependency scanning tool** — parses manifest files per ecosystem
  (`requirements.txt`, `pyproject.toml`, `go.mod`, `pom.xml`, `build.gradle`, `package.json`),
  queries registries for latest versions, and reports outdated dependencies with severity
- **Registry query tools** — PyPI, Go module proxy, Maven Central, and npm registry clients for
  fetching current package versions and changelogs
- **Changelog fetching tool** — retrieves release notes for a dependency version from its upstream
  source to populate migration context in analysis reports
- **Update dependencies tool** — applies version updates directly to manifest files inside the
  sandbox using ecosystem-specific patch commands; supports multi-manifest repositories
- **validate_project tool** — runs the project's build and test suite inside the sandbox after
  applying updates; auto-detects Maven vs Gradle for Java, pip extras for Python tests;
  populates `confidence` field in `ScanAnalysisReport`
- **Java ecosystem support** — `pom.xml` and `build.gradle` parsers; Maven versions plugin and
  Gradle manifest patch for version updates; Maven Central registry queries; Maven and Gradle
  build/test validation with auto-detection
- **Go ecosystem support** — `go.mod` parser; `go get` update commands; Go module proxy registry
  queries; `go build` and `go test` validation
- **Python ecosystem support** — `requirements.txt` and `pyproject.toml` parsers; pip/uv update
  commands; PyPI registry queries; pytest validation with test extras auto-detection
- **Package analyzer subagent** — dedicated sub-agent for deep per-package upgrade analysis,
  improving confidence scoring and change summarisation
- **Pydantic schemas** — `Ecosystem`, `DependencyInfo`, `ScanAnalysisReport`, `JobState`,
  `JobStatus`, `ScanWebhookPayload`; `ScanAnalysisReport` includes breaking change extraction
  and outdated dependency cap
- **Centralized configuration** — `pydantic-settings`-based `Settings` with `.env` support;
  `ANTHROPIC_API_KEY`, `LANGFUSE_*`, `GITHUB_TOKEN`, `K8S_*` and HTTP client tuning knobs
- **Kubernetes sandbox** — langchain-kubernetes `KubernetesProvider` in `agent-sandbox` mode;
  runtime server (`k8s/runtime/`) provides Python 3, Node.js 22, Go 1.23, Rust in a
  hardened non-root container; deny-all NetworkPolicy; RBAC service account for the agent pod
- **Sandbox router** — lightweight FastAPI reverse proxy (`k8s/sandbox-router/`) routing agent
  requests to the correct sandbox pod; hash-verified pip installation
- **Observability** — Langfuse tracing on every agent invocation; OpenAI model support alongside
  Anthropic for model flexibility

[Unreleased]: https://github.com/bitkaio/migratowl/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bitkaio/migratowl/releases/tag/v0.1.0
