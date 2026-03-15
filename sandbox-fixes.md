# Sandbox timeout fix — what was actually wrong

## Symptom

`_wait_for_sandbox_ready()` timed out after 120s every time. The agent fell back to `StateBackend` (no `execute` tool). Logs showed:

```
Creating SandboxClaim 'sandbox-claim-...' ...
Watching for Sandbox to become ready...
```

Then silence for 120 seconds, followed by the fallback.

## What the original hypothesis was (wrong)

API group version skew: the SDK watches `agents.x-k8s.io` for Sandbox resources, but the controller creates them in `extensions.agents.x-k8s.io`.

**This turned out to be incorrect.** Diagnostics showed:

- The CRD `sandboxes.agents.x-k8s.io` exists — the SDK constant is correct
- `sandboxes.extensions.agents.x-k8s.io` does NOT exist as a CRD
- The controller reconciler logs confirm it watches `agents.x-k8s.io`

Changing the API group would have broken things further.

## What was actually wrong (two issues)

### Issue 1: No runtime server in the container image (root cause of timeout)

The `SandboxTemplate` used `python:3.12-slim` as the container image. This image has no long-running process — it starts, finds nothing to run, and exits immediately. All warm pool pods were in `CrashLoopBackOff`:

```
python-warm-pool-2lr6n   0/1     CrashLoopBackOff   8 (5m4s ago)    20m
python-warm-pool-p6dds   0/1     Completed          9 (5m27s ago)   21m
python-warm-pool-p9nml   0/1     Completed          9 (5m47s ago)   21m
```

The controller reported the warm pool as 3/3 (it counts pods regardless of status), so this wasn't obvious from pool metrics alone.

The agent-sandbox architecture requires each sandbox Pod to run an HTTP runtime server on port 8888 that handles `/execute`, `/upload`, `/download`, `/list`, and `/exists` endpoints. The official example is at `kubernetes-sigs/agent-sandbox/examples/python-runtime-sandbox/` — a FastAPI + uvicorn server.

**Fix:** Built a `sandbox-runtime:latest` image containing the runtime server (`k8s/runtime/`), updated the template to use it.

### Issue 2: Tunnel targets missing sandbox-router service

The SDK's `_start_and_wait_for_port_forward()` hardcodes `svc/sandbox-router-svc:8080` as the port-forward target. The sandbox-router is a reverse proxy that routes requests to sandbox Pods based on `X-Sandbox-ID` headers — but it's a separate component that needs to be built and deployed. We didn't deploy it.

```
Error from server (NotFound): services "sandbox-router-svc" not found
```

Even after fixing Issue 1, the tunnel would have crashed immediately.

**Fix:** Patched `sandbox_client.py` to port-forward directly to the sandbox Pod (`pod/{pod_name}:{server_port}`) instead of going through the Router.

## Files changed

| File | Change |
|------|--------|
| `k8s/runtime/main.py` | New — FastAPI runtime server (execute, upload, download, list, exists) |
| `k8s/runtime/Dockerfile` | New — builds `sandbox-runtime:latest` from `python:3.12-slim` + runtime |
| `k8s/runtime/requirements.txt` | New — fastapi, uvicorn, python-multipart |
| `k8s/sandbox-template.yaml` | Changed image from `python:3.12-slim` to `sandbox-runtime:latest` |
| `.venv/.../sandbox_client.py` | Patched tunnel to target `pod/{pod_name}` instead of `svc/sandbox-router-svc` |

## How to rebuild the runtime image

```bash
cd k8s/runtime
minikube image build -t sandbox-runtime:latest .
```

Then re-apply the template and delete old pods:

```bash
kubectl apply -f k8s/sandbox-template.yaml
kubectl delete pods -n default -l agents.x-k8s.io/pool
```

## Verification

After fixes, the full lifecycle works:

1. Warm pool pods stay Running (1/1 Ready)
2. SandboxClaim creates a Sandbox with `Ready: True` in <1s
3. Runtime responds on port 8888: `{"status":"ok"}`
4. Execute endpoint works: `POST /execute {"command":"python3 -c 'print(2+2)'"} → {"stdout":"4\n","exit_code":0}`
