# Development Setup

Local development environment for MigratOwl using minikube and the agent-sandbox controller.

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- [minikube](https://minikube.sigs.k8s.io/docs/start/)
- [kubectl](https://kubernetes.io/docs/tasks/tools/)
- [pnpm](https://pnpm.io/) (for deep-agents-ui)

## 1. Install Python dependencies

```bash
uv sync
```

## 2. Configure environment

Copy the example and fill in your API keys:

```bash
cp .env.example .env
```

Required variables:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Optional overrides (defaults shown):

```
SANDBOX_TEMPLATE=python-sandbox-template
SANDBOX_NAMESPACE=default
```

## 3. Start minikube

```bash
minikube start --driver=docker --memory=8192 --cpus=4
```

Verify the cluster is running:

```bash
kubectl cluster-info
```

## 4. Install the agent-sandbox controller

```bash
export VERSION="v0.1.0"
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${VERSION}/manifest.yaml
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${VERSION}/extensions.yaml
```

> **Note:** The sandbox-router component is not needed. `langchain-kubernetes` uses `connection_mode="tunnel"` (kubectl port-forward) by default, which connects directly to sandbox Pods.

Wait for the controller to be ready:

```bash
kubectl get pods -n agent-sandbox-system -w
```

Verify CRDs are installed:

```bash
kubectl get crds | grep agents.x-k8s.io
```

## 5. Apply project manifests

```bash
kubectl apply -f k8s/sandbox-template.yaml
kubectl apply -f k8s/warm-pool.yaml  # optional — pre-warms 1 sandbox pod
```

Verify:

```bash
kubectl get sandboxtemplates       # should show python-sandbox-template
kubectl get sandboxwarmpools       # should show python-warm-pool (if applied)
```

## 6. Run the agent

```bash
uv run langgraph dev
```

This starts the LangGraph dev server at `http://localhost:2024`. The server starts immediately — sandbox creation begins **eagerly in a background thread** at startup (not on the event loop, avoiding blockbuster's `BlockingError`). It is typically ready by the time you send your first message. Check the logs for:

- `Kubernetes sandbox created: <id>` — sandbox is ready
- `Kubernetes sandbox unavailable — falling back to StateBackend` — K8s not reachable, agent runs without the `execute` tool

## 7. Set up deep-agents-ui

In a separate terminal:

```bash
cd ~/Projects
git clone https://github.com/langchain-ai/deep-agents-ui.git
cd deep-agents-ui
pnpm install
pnpm dev
```

Open the UI in your browser, connect to `http://localhost:2024`, and select the `migratowl` graph.

### Smoke test

Send this message in the UI:

> Write a Python script that computes the first 10 Fibonacci numbers and run it.

The agent should use the `execute` tool to run the script in the K8s sandbox and return the output.

## Cleanup

When you stop `langgraph dev` (Ctrl+C), the sandbox pod is automatically deleted via the `atexit` handler.

To verify no orphaned pods remain:

```bash
kubectl get pods -n default
```

To stop minikube:

```bash
minikube stop
```

## Troubleshooting

### First sandbox is slow to start

Without the warm pool, expect 5-30s on first creation due to pod scheduling and image pull. Apply `k8s/warm-pool.yaml` to reduce this to sub-second.

### Port-forward drops on long sessions

The `connection_mode="tunnel"` setting uses `kubectl port-forward` under the hood, which can drop on long-running sessions. Restart `langgraph dev` if the sandbox becomes unresponsive.

### gVisor on macOS

gVisor is intentionally omitted from the sandbox template. It can be flaky on minikube with the Docker driver on macOS. To enable it later:

1. `minikube addons enable gvisor`
2. Add `runtimeClassName: gvisor` to `k8s/sandbox-template.yaml` under `podTemplate.spec`

### NetworkPolicy not enforced

The default `kindnet` CNI in minikube does not support NetworkPolicy. For network isolation, switch to Calico:

```bash
minikube start --cni=calico --driver=docker --memory=4096 --cpus=4
```

Or disable network blocking by setting `block_network=False` in the provider config for local testing.

### SSL certificate error with minikube (`SSLCertVerificationError`)

`langgraph-api` injects `truststore` at startup, which patches Python's `ssl.SSLContext` to use macOS Keychain for certificate validation. Minikube's self-signed CA cert fails this strict check with:

```
ssl.SSLCertVerificationError: ('"minikube" certificate is not standards compliant',)
```

The code in `migratowl/agent.py` handles this automatically by temporarily extracting truststore during Kubernetes client initialization (this happens in a background thread at startup). If you still see SSL errors, verify your minikube CA cert exists:

```bash
ls ~/.minikube/ca.crt
```

If missing, recreate it with `minikube delete && minikube start`.
