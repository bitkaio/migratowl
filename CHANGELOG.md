# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
