
<p align="center">
  <img src="assets/migratowl-logo.png" alt="Migratowl" height="320" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.13%2B-blue" alt="Python 3.13+" />
  <img src="https://img.shields.io/badge/license-BSD%203--Clause-green" alt="License" />
</p>

<p align="center">AI-powered dependency migration analyzer — discovers breaking upgrades, explains exactly what failed, and tells you how to fix it.</p>

---

## What It Does

Migratowl answers one question: **"If I upgrade this dependency, will anything break — and how do I fix it?"**

It receives a webhook, clones the target repository, scans all dependency manifests, queries package registries for newer versions, and runs the project inside an isolated Kubernetes sandbox with every dependency bumped. An AI agent executes the test suite, reads the error output, fetches the relevant changelog, and produces a structured report per dependency.

The result tells developers:
- Whether the upgrade is breaking
- What specifically went wrong
- A verbatim citation from the changelog
- A plain-English fix suggestion
- A confidence score (0.0–1.0)

---

## Table of Contents

- [Supported Ecosystems](#supported-ecosystems)
- [How It Works](#how-it-works)
- [Quick Start](#quick-start)
- [API Reference](#api-reference)
  - [POST /webhook](#post-webhook)
  - [GET /jobs/{job_id}](#get-jobsjob_id)
  - [GET /healthz](#get-healthz)
- [Response Schema](#response-schema)
- [Configuration](#configuration)
- [Kubernetes Setup](#kubernetes-setup)
- [Observability](#observability)
- [Architecture](#architecture)
- [Project Layout](#project-layout)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

---

## Supported Ecosystems

| Language | Manifest files | Registry |
|----------|----------------|----------|
| Python | `pyproject.toml`, `requirements.txt` | PyPI |
| Node.js | `package.json` | npm |
| Go | `go.mod` | proxy.golang.org |
| Rust | `Cargo.toml` | crates.io |
| Java | `pom.xml` (Maven), `build.gradle` (Gradle) | Maven Central |

---

## How It Works

Migratowl runs a four-phase agent workflow inside an ephemeral Kubernetes sandbox.

```
POST /webhook
     │
     ▼
┌─────────────────────────────────────────────────────────┐
│  Phase 1 — Setup                                        │
│                                                         │
│  clone_repo ──► detect_languages ──► scan_dependencies  │
│                                           │             │
│                                    check_outdated_deps  │
└─────────────────────────┬───────────────────────────────┘
                          │ outdated dep list
                          ▼
┌─────────────────────────────────────────────────────────┐
│  Phase 2 — Main Analysis                                │
│                                                         │
│  copy_source("main") ──► update_dependencies (all)      │
│                               │                         │
│                        execute_project (install + test) │
└─────────────────────────┬───────────────────────────────┘
                          │ pass / fail + error output
                          ▼
┌─────────────────────────────────────────────────────────┐
│  Phase 3 — Confidence Scoring                           │
│                                                         │
│  All pass ──► every package: is_breaking=false, conf=1  │
│                                                         │
│  Some fail ──► assign confidence per package            │
│    conf ≥ threshold ──► fetch_changelog + write report  │
│    conf < threshold ──► delegate to package-analyzer    │
│                          subagent (isolated run)        │
└─────────────────────────┬───────────────────────────────┘
                          │ AnalysisReport[]
                          ▼
┌─────────────────────────────────────────────────────────┐
│  Phase 4 — Compile Results                              │
│                                                         │
│  Merge reports from main agent + subagents              │
│  ──► ScanAnalysisReport (POST to callback_url)          │
└─────────────────────────────────────────────────────────┘
```

**Confidence scoring rules** (applied in Phase 3 when tests fail):
- Error message directly names the package → high confidence (≥ 0.8)
- Import or attribute error for a known package API → high confidence
- Major version jump (e.g. `2.x → 3.x`) → moderate confidence boost
- Generic failure with no clear link → low confidence (< 0.5)

The default confidence threshold is `0.7` (configurable via `MIGRATOWL_CONFIDENCE_THRESHOLD`).

**Sandbox workspace layout:**
```
/home/user/workspace/
├── source/          # Immutable clone — never executed
├── main/            # All deps bumped, executed in Phase 2
└── <package-name>/  # Per-package isolation (created on demand by subagent)
```

---

## Quick Start

**Prerequisites:** Python 3.13+, [uv](https://docs.astral.sh/uv/), Docker, minikube, kubectl.

```bash
# 1. Install dependencies
uv sync

# 2. Configure environment
cp .env.example .env
# Edit .env — set at minimum: ANTHROPIC_API_KEY

# 3. Start local Kubernetes cluster
minikube start --driver=docker --memory=8192 --cpus=4

# 4. Install agent-sandbox controller and CRDs
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.1.0/manifest.yaml
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.1.0/extensions.yaml

# 5. Build sandbox runner image inside minikube
eval $(minikube docker-env)
docker build -t sandbox-runtime:latest k8s/runtime/

# 6. Apply RBAC and sandbox template
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/sandbox-template.yaml

# 7. Start the server
uv run uvicorn migratowl.api.main:app --reload
```

Trigger a scan:

```bash
curl -X POST http://localhost:8000/webhook \
  -H 'Content-Type: application/json' \
  -d '{
    "repo_url": "https://github.com/org/repo",
    "callback_url": "https://yourservice.example.com/results"
  }'
# → {"job_id": "...", "status_url": "/jobs/..."}
```

---

## API Reference

### POST /webhook

Accepts a scan request. Returns `202 Accepted` immediately; analysis runs in the background and POSTs the result to `callback_url` when done.

**Request body** (`ScanWebhookPayload`):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `repo_url` | `string` | **required** | Git repository URL to scan |
| `branch_name` | `string` | `"main"` | Branch to clone and analyze |
| `git_provider` | `string` | `"github"` | Git provider (currently informational) |
| `pr_number` | `integer \| null` | `null` | Pull request number (informational) |
| `callback_url` | `string \| null` | `null` | URL to POST `ScanAnalysisReport` on completion |
| `exclude_deps` | `string[]` | `[]` | Dependency names to skip |
| `max_deps` | `integer` | `50` | Maximum outdated deps to analyze (must be > 0) |
| `ecosystems` | `string[] \| null` | `null` | Limit to specific ecosystems: `"python"`, `"nodejs"`, `"go"`, `"rust"`, `"java"`. `null` = auto-detect all |

**Example:**

```json
{
  "repo_url": "https://github.com/org/repo",
  "branch_name": "main",
  "git_provider": "github",
  "callback_url": "https://yourservice.example.com/results",
  "exclude_deps": ["boto3"],
  "max_deps": 20,
  "ecosystems": ["python"]
}
```

**202 response** (`WebhookAcceptedResponse`):

```json
{
  "job_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status_url": "/jobs/3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

---

### GET /jobs/{job_id}

Poll the status of a scan job.

**Response** (`JobStatus`):

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | `string` | UUID assigned at webhook acceptance |
| `state` | `string` | Job lifecycle state (see below) |
| `created_at` | `datetime` | ISO 8601, UTC |
| `updated_at` | `datetime` | ISO 8601, UTC |
| `payload` | `ScanWebhookPayload` | Original request payload |
| `result` | `ScanAnalysisReport \| null` | Set when `state = "completed"` |
| `error` | `string \| null` | Set when `state = "failed"` |

**Job lifecycle:**

```
PENDING ──► RUNNING ──► COMPLETED
                   └──► FAILED
```

| State | Meaning |
|-------|---------|
| `pending` | Queued, not yet started (v1 runs one scan at a time) |
| `running` | Agent is actively analyzing the repository |
| `completed` | Analysis finished; `result` is populated |
| `failed` | Unrecoverable error; `error` describes what went wrong |

**404** when `job_id` is not found.

---

### GET /healthz

Liveness check. Returns `200 {"status": "ok"}` when the server is running.

---

## Response Schema

The `ScanAnalysisReport` delivered to `callback_url` (and returned in `GET /jobs/{job_id}` when completed):

```
ScanAnalysisReport
├── repo_url                  string    — repository that was analyzed
├── branch_name               string    — branch that was cloned
├── scan_result               ScanResult
│   ├── all_deps              Dependency[]   — every declared dependency found
│   │   ├── name              string
│   │   ├── current_version   string
│   │   ├── ecosystem         string
│   │   └── manifest_path     string
│   ├── outdated              OutdatedDependency[]  — deps with newer versions
│   │   ├── name              string
│   │   ├── current_version   string
│   │   ├── latest_version    string
│   │   ├── ecosystem         string
│   │   ├── manifest_path     string
│   │   ├── homepage_url      string | null
│   │   ├── repository_url    string | null
│   │   └── changelog_url     string | null
│   ├── manifests_found       string[]  — manifest file paths discovered
│   └── scan_duration_seconds float
├── reports                   AnalysisReport[]  — one per analyzed package
│   ├── dependency_name       string
│   ├── is_breaking           bool
│   ├── error_summary         string    — what failed (empty if not breaking)
│   ├── changelog_citation    string    — verbatim excerpt from changelog
│   ├── suggested_human_fix   string    — plain-English remediation step
│   └── confidence            float     — 0.0–1.0
├── skipped                   string[]  — package names not analyzed
└── total_duration_seconds    float
```

**Example report entry:**

```json
{
  "dependency_name": "requests",
  "is_breaking": true,
  "error_summary": "ImportError: cannot import name 'PreparedRequest'",
  "changelog_citation": "## 3.0.0 — Removed PreparedRequest from the public API.",
  "suggested_human_fix": "Replace `from requests import PreparedRequest` with `requests.models.PreparedRequest`.",
  "confidence": 0.95
}
```

---

## Configuration

All `MIGRATOWL_*` variables are optional (defaults shown). Third-party SDK keys use their standard names without the `MIGRATOWL_` prefix.

### LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Required when `MIGRATOWL_MODEL_PROVIDER=anthropic` (default) |
| `OPENAI_API_KEY` | — | Required when `MIGRATOWL_MODEL_PROVIDER=openai` |
| `MIGRATOWL_MODEL_PROVIDER` | `anthropic` | LLM provider: `anthropic` or `openai` |
| `MIGRATOWL_MODEL_NAME` | `claude-sonnet-4-6` | Model name (must match provider) |
| `MIGRATOWL_MODEL_RATE_LIMIT_RPS` | `0.1` | Max LLM requests/second (0.1 = 6 req/min) |
| `ANTHROPIC_BASE_URL` | — | Custom base URL for Anthropic API |
| `OPENAI_BASE_URL` | — | Custom base URL for OpenAI API |

### Kubernetes Sandbox

| Variable | Default | Description |
|----------|---------|-------------|
| `MIGRATOWL_SANDBOX_TEMPLATE` | `migratowl-sandbox-template` | agent-sandbox `AgentSandboxTemplate` name |
| `MIGRATOWL_SANDBOX_NAMESPACE` | `default` | Kubernetes namespace for sandbox pods |
| `MIGRATOWL_SANDBOX_CONNECTION_MODE` | `tunnel` | Connection mode: `tunnel` or `direct` |
| `MIGRATOWL_WORKSPACE_PATH` | `/home/user/workspace` | Workspace root inside the sandbox |

### Analysis

| Variable | Default | Description |
|----------|---------|-------------|
| `MIGRATOWL_CONFIDENCE_THRESHOLD` | `0.7` | Packages above this are analyzed directly; below → subagent |
| `MIGRATOWL_SCAN_REGISTRY_CONCURRENCY` | `10` | Concurrent registry queries when checking outdated deps |
| `MIGRATOWL_MAX_OUTPUT_CHARS` | `30000` | Truncation limit for sandbox command output |
| `MIGRATOWL_MAX_CHANGELOG_CHARS` | `15000` | Truncation limit for fetched changelogs |
| `MIGRATOWL_MAX_OUTDATED_DEPS` | `100` | Hard cap on registry scan results |

### HTTP Client

| Variable | Default | Description |
|----------|---------|-------------|
| `MIGRATOWL_HTTP_TIMEOUT` | `30.0` | Outbound request timeout (seconds) |
| `MIGRATOWL_HTTP_RETRY_COUNT` | `3` | Retries on 429 / 5xx responses |
| `MIGRATOWL_HTTP_RETRY_BACKOFF_BASE` | `0.5` | Base delay (seconds) for exponential backoff |

### API Server

| Variable | Default | Description |
|----------|---------|-------------|
| `MIGRATOWL_API_HOST` | `0.0.0.0` | Bind address |
| `MIGRATOWL_API_PORT` | `8000` | Bind port |

### Observability

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGFUSE_PUBLIC_KEY` | — | Enables LangFuse tracing when both keys are set |
| `LANGFUSE_SECRET_KEY` | — | See above |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` | LangFuse instance URL |

---

## Kubernetes Setup

Migratowl uses [langchain-kubernetes](https://github.com/bitkaio/langchain-kubernetes) in **agent-sandbox mode** by default, which requires the [`kubernetes-sigs/agent-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox) controller and CRDs installed in your cluster. This provides warm pod pools and gVisor/Kata isolation.

```bash
# Install controller + CRDs (one-time)
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.1.0/manifest.yaml
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.1.0/extensions.yaml

# Build runtime image (must be visible to the cluster — use minikube docker-env locally)
eval $(minikube docker-env)
docker build -t sandbox-runtime:latest k8s/runtime/

# Apply manifests
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/sandbox-template.yaml
```

**Optional warm pool** (reduces cold-start latency):
```bash
kubectl apply -f k8s/warm-pool.yaml
```

**Raw mode fallback** — if you can't install the agent-sandbox controller, switch to raw mode (works on any cluster, no CRDs required):
```bash
MIGRATOWL_SANDBOX_CONNECTION_MODE=direct  # set in .env
```
Then install `langchain-kubernetes[raw]` instead of `langchain-kubernetes[agent-sandbox]`. Raw mode manages ephemeral pods directly and attaches a deny-all `NetworkPolicy` for isolation.

**Security defaults applied to every pod:**
- `runAsNonRoot: true`, `runAsUser: 1000`
- `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`
- `automountServiceAccountToken: false`
- Deny-all `NetworkPolicy` (ingress + egress)

---

## Observability

Migratowl integrates with [LangFuse](https://langfuse.com) for trace-level observability. Tracing is off by default and activates when both keys are present.

```bash
# .env
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com   # or your self-hosted instance
```

When enabled, every scan produces a LangFuse session (keyed by `job_id`) containing:
- **Main agent trace** — all LLM calls and tool invocations
- **Tool call spans** — `clone_repo`, `scan_dependencies`, `execute_project`, etc.
- **Subagent spans** — `package-analyzer` subagent runs nested under the parent trace

No additional code changes are needed — the `observability.py` module initializes the handler at startup and patches the LangGraph graph to inject session IDs automatically.

---

## Architecture

```
                          ┌─────────────────────────────┐
  HTTP client             │          FastAPI             │
  ─────────────────────► │  POST /webhook               │
                          │  GET  /jobs/{id}             │
                          │  GET  /healthz               │
                          └──────────────┬──────────────┘
                                         │ asyncio.create_task
                                         ▼
                          ┌─────────────────────────────┐
                          │     Migratowl Agent         │
                          │  (deepagents / LangGraph)   │
                          │                             │
                          │  Tools:                     │
                          │  • clone_repo               │
                          │  • detect_languages         │
                          │  • scan_dependencies        │
                          │  • check_outdated_deps      │
                          │  • copy_source              │
                          │  • update_dependencies      │
                          │  • execute_project          │
                          │  • fetch_changelog          │
                          │  • read_manifest            │
                          │  • patch_manifest           │
                          │                             │
                          │  Subagent:                  │
                          │  • package-analyzer         │
                          └──────────────┬──────────────┘
                                         │ executes via
                                         ▼
                          ┌─────────────────────────────┐
                          │   Kubernetes Sandbox        │
                          │  (langchain-kubernetes)     │
                          │                             │
                          │  Ephemeral Pod              │
                          │  • Non-root, no caps        │
                          │  • Deny-all NetworkPolicy   │
                          │  • gVisor / Kata isolation  │
                          └─────────────────────────────┘
```

---

## Project Layout

```
migratowl/
├── api/
│   ├── main.py          # FastAPI app, /webhook + /jobs endpoints, lifespan
│   ├── jobs.py          # In-memory JobStore (PENDING→RUNNING→COMPLETED|FAILED)
│   └── helpers.py       # build_user_message, extract_report
├── agent/
│   ├── graph.py         # graph singleton + sandbox lifecycle (langgraph.json entrypoint)
│   ├── factory.py       # create_migratowl_agent() — builds the LangGraph
│   ├── sandbox.py       # KubernetesProvider init/teardown helpers
│   ├── subagents.py     # package-analyzer subagent definition
│   ├── session_graph.py # Patches ainvoke/astream to inject LangFuse session IDs
│   └── tools/
│       ├── clone.py     # clone_repo, copy_source
│       ├── detect.py    # detect_languages
│       ├── scan.py      # scan_dependencies
│       ├── registry.py  # check_outdated_deps
│       ├── update.py    # update_dependencies
│       ├── execute.py   # execute_project (runs install + test in sandbox)
│       ├── changelog.py # fetch_changelog (PyPI / npm / GitHub / raw HTTP)
│       └── manifest.py  # read_manifest, patch_manifest (sandbox file I/O)
├── models/
│   └── schemas.py       # All Pydantic models (ScanWebhookPayload, ScanAnalysisReport, …)
├── config.py            # pydantic-settings Settings class (MIGRATOWL_ prefix)
├── observability.py     # LangFuse CallbackHandler setup + session ID injection
├── registry.py          # Registry query logic (PyPI, npm, crates.io, Go proxy)
├── parsers.py           # Manifest parsers per ecosystem
├── changelog.py         # Changelog fetch strategies (multi-strategy fallback)
├── patches.py           # Dependency version patching helpers
└── http.py              # Shared HTTPX async client with retry logic

k8s/
├── rbac.yaml            # ServiceAccount + ClusterRole for sandbox management
├── sandbox-template.yaml# AgentSandboxTemplate CRD for the runner pod
├── warm-pool.yaml       # Optional warm pool for faster pod startup
├── sandbox-router.yaml  # Optional sandbox router service
└── runtime/             # Dockerfile + entrypoint for the sandbox runner image

tests/                   # Mirrors migratowl/ package structure
```

---

## Development

| Task | Command |
|------|---------|
| Install | `uv sync` |
| Run | `uv run uvicorn migratowl.api.main:app --reload` |
| Test | `uv run pytest tests/ -v` |
| Lint | `uv run ruff check migratowl/` |

**TDD is mandatory** for all production code in `migratowl/`. The Red-Green-Refactor cycle is enforced: write a failing test first, confirm RED, write minimal code to pass, confirm GREEN, then refactor. No production code without a corresponding test in `tests/`. See `CLAUDE.md` for details.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All contributors must sign the [CLA](CLA.md).

1. Open an issue first
2. Branch: `issue/<NUMBER>-short-description`
3. Write a failing test before any production code (TDD — no exceptions)
4. Open a PR with `Closes #<NUMBER>`

---

## License

BSD 3-Clause — see [LICENSE](LICENSE).
