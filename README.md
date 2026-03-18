# MigratOwl

AI-powered dependency migration analyzer. Automates the discovery, investigation, and reporting of breaking dependency upgrades across multi-language codebases.

---

## What It Does

MigratOwl answers one question: **"If I upgrade this dependency, will anything break — and how do I fix it?"**

It receives a webhook, clones the target repository, scans all dependency manifests, queries package registries for newer versions, and runs the project inside an isolated Kubernetes sandbox with every dependency bumped. An AI agent executes the test suite, reads the error output, fetches the relevant changelog, and produces a structured report per dependency.

The result tells developers:
- Whether the upgrade is breaking
- What specifically went wrong
- A verbatim citation from the changelog
- A plain-English fix suggestion
- A confidence score

## Supported Ecosystems

| Language | Manifest | Registry |
|----------|----------|----------|
| Python | `pyproject.toml`, `requirements.txt` | PyPI |
| Node.js | `package.json` | npm |
| Go | `go.mod` | proxy.golang.org |
| Rust | `Cargo.toml` | crates.io |

## How It Works

1. **Webhook trigger** — `POST /webhook` with a repo URL. Returns `202 Accepted` immediately; analysis runs in the background and `POST`s results to an optional `callback_url`.
2. **Dependency scan** — clone repo, parse all manifests, query registries, identify outdated dependencies.
3. **Sandboxed investigation** — a deepagents agent runs inside an ephemeral Kubernetes pod. It bumps all deps, runs the test suite, and correlates errors with changelog entries.
4. **Report delivery** — structured `ScanAnalysisReport` delivered to `callback_url`.

## Webhook Interface

```
POST /webhook
Content-Type: application/json
```

```json
{
  "repo_url": "https://github.com/org/repo",
  "branch_name": "main",
  "callback_url": "https://yourservice.example.com/results",
  "ecosystems": ["python"],
  "exclude_deps": [],
  "max_deps": 50
}
```

Response payload (sent to `callback_url` on completion):

```json
{
  "repo_url": "...",
  "branch_name": "main",
  "scan_result": { "outdated": [...], "manifests_found": [...] },
  "reports": [
    {
      "dependency_name": "requests",
      "is_breaking": true,
      "error_summary": "ImportError: cannot import name 'PreparedRequest'",
      "changelog_citation": "## 3.0.0 — Removed PreparedRequest from the public API.",
      "suggested_human_fix": "Replace `from requests import PreparedRequest` with `requests.models.PreparedRequest`.",
      "confidence": 0.95
    }
  ],
  "skipped": [],
  "total_duration_seconds": 87.4
}
```

## Stack

- **Python 3.13+** / **FastAPI** / **asyncio**
- **deepagents** — LangChain agent harness (LangGraph-based) for AI investigation
- **claude-sonnet-4-6** — LLM via `langchain-anthropic`
- **langchain-kubernetes** — Kubernetes sandbox backend (no SaaS, code stays in-cluster)

## Security

All untrusted build and test commands run inside ephemeral Kubernetes pods:

- Non-root, dropped capabilities, no privilege escalation
- Deny-all `NetworkPolicy` (egress allowed only to package registries)
- Pod destroyed after every analysis

## Setup

**Prerequisites**: Python 3.13+, [uv](https://docs.astral.sh/uv/), Docker, minikube, kubectl.

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env   # add ANTHROPIC_API_KEY

# Start local Kubernetes cluster
minikube start --driver=docker --memory=8192 --cpus=4

# Install agent-sandbox controller
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.1.0/manifest.yaml
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.1.0/extensions.yaml

# Build sandbox runner image inside minikube
eval $(minikube docker-env)
docker build -t sandbox-runtime:latest k8s/runtime/

# Apply Kubernetes manifests
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/sandbox-template.yaml
```

## Commands

| Task | Command |
|------|---------|
| Install | `uv sync` |
| Run | `uv run uvicorn migratowl.api.main:app --reload` |
| Test | `uv run pytest tests/ -v` |
| Lint | `uv run ruff check migratowl/` |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). All contributors must sign the [CLA](CLA.md).

1. Open an issue first
2. Branch: `issue/<NUMBER>-short-description`
3. Write a failing test before any production code (TDD — no exceptions)
4. Open a PR with `Closes #<NUMBER>`

## License

BSD 3-Clause — see [LICENSE](LICENSE).
