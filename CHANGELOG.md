# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **Release jobs used the shared uv cache** — every workflow passed `enable-caching` to `astral-sh/setup-uv`, which
  has no such input (it is `enable-cache`), so the setting was ignored and the default (`auto`, on) applied. The
  release workflow, which meant to disable the cache against cache poisoning, ran with it. The input is now spelled
  correctly, and all workflows pin the same `setup-uv` v7 commit; `migratowl-scan.yml` pinned the v7 tag object
  instead of its commit, which GitHub cannot resolve.

- **PR comments rendered LLM text unescaped** — fix suggestions (partly derived from untrusted changelogs) and
  package names went into the PR/MR comment as raw Markdown, so a `|` broke the table and the text could
  ping people with `@mentions`, embed tracking images or inject HTML. Table cells are now escaped and kept
  on one line, HTML is escaped, mentions and image embeds are neutralised, package names are restricted to
  normal name characters, and each field is capped at 1000 characters.

- **agent-sandbox pods were not hardened** — `k8s/sandbox-template.yaml` set no security context, so
  sandbox pods ran untrusted repository code with the image defaults and a mounted service account token,
  while the README claimed otherwise. The template now sets `automountServiceAccountToken: false`,
  `runAsNonRoot` with UID/GID 1000, seccomp `RuntimeDefault`, `allowPrivilegeEscalation: false` and drops
  all capabilities (matching raw mode). gVisor/Kata is documented as an opt-in `runtimeClassName`. The
  README now describes the security settings of each mode accurately. Re-apply the template.

- **Optional API authentication and callback SSRF protection** — new `MIGRATOWL_API_TOKEN`: when set,
  `POST /webhook` and all `/jobs` endpoints require `Authorization: Bearer <token>` (`/healthz` stays
  open); when unset, the server logs a startup warning. `callback_url` must be an http(s) URL and may not
  target private, loopback or link-local addresses (`422`); hostnames are resolved again before the
  callback is sent and redirects are not followed. `MIGRATOWL_CALLBACK_ALLOW_PRIVATE=true` lifts the
  address check for local development. `docs/examples/with-migratowl-server.yml` sends the token from the
  `MIGRATOWL_API_TOKEN` repository secret.

### Changed

- **Dependencies refreshed within their current majors** — FastAPI 0.141, pydantic 2.14, pydantic-settings 2.15,
  langchain-core 1.6, langchain 1.4, langgraph 1.2.13, langfuse 4.15, plus pytest, ruff and langgraph-cli in the dev
  group. The dev group now requires `langgraph-api>=0.11.1`; without it the resolver picked an older `langgraph dev`
  server in exchange for a newer OpenTelemetry. The LLM SDK majors (`anthropic` 1.x, `openai` 3.x) are not part of
  this update.

- **README brought up to date** — documents `GET /jobs?state=`, `POST /jobs/{id}/resume` and the `interrupted`
  state, the crash-recovery and concurrency settings (`MIGRATOWL_PERSISTENCE_BACKEND`, `*_DB_PATH`,
  `MAX_SCAN_RETRIES`, `MAX_CONCURRENT_SCANS`, `SANDBOX_TTL_*`), every field of the report, and the current module
  layout. `.env.example` lists the same settings, and a test now fails when a setting is missing from either.

- **`langchain-kubernetes` now comes from the maintained fork** — the dependency is installed from
  `github.com/barnakun/langchain-kubernetes` at tag `py-0.4.1` (MIT, forked from `bitkaio/langchain-kubernetes`
  0.4.0) instead of PyPI. 0.4.1 deletes a raw-mode sandbox's NetworkPolicy together with its Pod (previously one
  policy leaked per scan) and reopens the sandbox-router tunnel when reconnecting, so
  `POST /jobs/{id}/resume` reuses the surviving sandbox instead of starting over. Installing now needs `git`.

- **Default model is now `claude-sonnet-5-5`** (was `claude-sonnet-5`; same price, $2/$10 per 1M tokens).
  With the Anthropic provider the agent now requests native structured output (`output_config.format`)
  instead of letting LangChain fall back to a forced tool call: Claude Sonnet 5.5, Opus 5.5 and Fable 5.1
  reject `tool_choice: any` with a 400, and the installed LangChain does not recognise them as
  structured-output models. OpenAI-compatible providers (`openai`, `litellm`) keep the automatic
  choice. Pin `MIGRATOWL_MODEL_NAME=claude-sonnet-5` to keep the previous model.

- **Scans run their mechanical phases in code** — cloning, dependency scanning, the outdated
  check, updating `main/` and validation now run as a fixed pipeline (`migratowl/pipeline.py`)
  instead of being driven by the LLM. `max_deps`, `exclude_deps`, `check_deps` and `ecosystems`
  are enforced in code. Packages whose ecosystem passes validation with a non-major bump are
  marked safe without an LLM call, and a scan with nothing to review makes no LLM call at all.
  The LLM gets a short brief and returns only per-package verdicts; the final report is assembled
  in code. The agent's tool list shrinks to `prepare_scan`, `fetch_changelog_tool` and
  `read_manifest`. Makes small/free models usable and cuts token use for every model.
  New setting `MIGRATOWL_ANALYSIS_TAIL_CHARS` (default `4000`).
- **`validate_project` keeps the tail of long output** — the failure summary that test runners
  print last is no longer cut off.

### Added

- **Lockfile-aware version checks** — `package-lock.json`, `uv.lock`, `poetry.lock` and `Cargo.lock` are read
  next to (or above) each manifest, and the installed version — not the declared range — decides whether a
  dependency is outdated, how large the upgrade is, and what the LLM brief shows. `Dependency` and
  `OutdatedDependency` gain an `installed_version` field. The new parsers are fuzzed.

- **Coverage-guided fuzzing for untrusted-input parsers** ([#12](https://github.com/bitkaio/migratowl/issues/12)) —
  Atheris harnesses (`fuzz/fuzz_parsers.py`, `fuzz/fuzz_changelog.py`) fuzz the manifest and changelog
  parsers, which ingest content from arbitrary upstream repos. A new **Fuzz Smoke** CI job runs each
  harness on every PR and fails on any discovered crash. Adds `atheris` to the dev dependency group
  (Linux-only). See [`fuzz/README.md`](fuzz/README.md).

- **Session awareness & crash recovery** — async scans now survive a process crash and can be resumed.
  Job state is persisted (SQLite by default; `MIGRATOWL_PERSISTENCE_BACKEND=memory` for CI/ephemeral),
  and the LangGraph agent state is checkpointed (`AsyncSqliteSaver`, keyed by `thread_id == job_id`).
  On restart, jobs orphaned by a crash are reconciled from `RUNNING`/`PENDING` to a new `INTERRUPTED`
  state (or `FAILED` past `MIGRATOWL_MAX_SCAN_RETRIES`). `POST /jobs/{id}/resume` re-runs an interrupted
  job — reconnecting to the surviving sandbox pod and resuming from the checkpoint when the pod is still
  alive, or clearing the checkpoint and restarting from scratch when it's gone (resuming stale reasoning
  against an empty sandbox would produce wrong results). New settings:
  - `MIGRATOWL_PERSISTENCE_BACKEND` (`sqlite` default / `memory`), `MIGRATOWL_JOBS_DB_PATH`,
    `MIGRATOWL_CHECKPOINT_DB_PATH`
  - `MIGRATOWL_MAX_SCAN_RETRIES` (default 3) — retry cap before an interrupted job is failed
  - `MIGRATOWL_MAX_CONCURRENT_SCANS` (default 1) — resource throttle for concurrent scans (each job runs
    in its own sandbox pod, so this bounds sandbox/LLM load rather than preventing collisions)
  - `MIGRATOWL_SANDBOX_TTL_SECONDS` (default none) / `MIGRATOWL_SANDBOX_TTL_IDLE_SECONDS` (default 1800)
    — TTLs for reaping leaked sandboxes; a guarded startup sweep cleans up orphaned pods
  - New endpoints: `GET /jobs?state=<state>` to list jobs by state; `POST /jobs/{id}/resume`
  - New dependencies: `aiosqlite`, `langgraph-checkpoint-sqlite`

- **Generic LLM proxy support** — developers using internal proxies (LiteLLM, Azure API Management, etc.)
  can now route LLM calls through their corporate infrastructure without code changes. Three new settings:
  - `MIGRATOWL_MODEL_PROVIDER=litellm` — uses the OpenAI SDK to call any LiteLLM-compatible endpoint
  - `LITELLM_BASE_URL` — unified proxy endpoint (e.g. `http://localhost:6655/litellm/v1`)
  - `MIGRATOWL_MODEL_ALIAS` — override model name when proxy expects different naming conventions
    (e.g. `anthropic--claude-sonnet-latest` instead of `claude-sonnet-5`)
  See [`docs/proxy-setup.md`](docs/proxy-setup.md) for configuration examples.

- **Local E2E test skill** — Claude Code users can now run `test it locally` to execute a full
  production-like scan using a Kind cluster and HAI/LiteLLM proxy. Automatically sets up the cluster,
  starts the server, triggers a scan, and reports results. See `.claude/skills/local-e2e-test.md`.

### Fixed

- **Java versions set through a pom property or a Gradle version catalog were ignored** — `<version>${spring.version}</version>`
  is now resolved from the pom's `<properties>` (following chained properties), and `gradle/libs.versions.toml` is scanned
  (`"group:artifact:version"`, inline `version` and `version.ref`; rich versions, BOM-managed entries and plugins are
  skipped). Updates patch the place that defines the version — the property or the `[versions]` key, shared by every
  library that uses it — instead of the dependency line, so validation tests the new version.

- **Go major versions were never reported** — a Go module's next major release lives at a new module path
  (`github.com/x/y` → `github.com/x/y/v2`), so the registry only ever saw versions of the current major. It now
  probes `/v{N+1}` onwards (up to five majors, skipping `gopkg.in/`) and reports the newest stable major as
  the latest version. Updating to it runs `go get` on the new path and rewrites the code's imports (including
  subpackages, skipping `vendor/`) before `go mod tidy`, so the requirement is not dropped again.

- **Changelogs were not found or not understood for many packages** — the registry now also treats a "Code"
  link or a GitHub/GitLab homepage as the repository (Sphinx, psutil); a "changelog" link that is just the
  repository page (aiofiles' `github.com/Tinche/aiofiles#history`) is used as a repository hint instead of
  being parsed as HTML; a short changelog file that only points elsewhere ("History has moved to: …") is
  followed; version ranges given as constraints (`>=5.9`, `^4.21.2`) filter correctly instead of keeping every
  release; and breaking changes written as `**Backward incompatible changes**`, `* breaking: …` or indented
  `#123, [Platform]: …` items are recognised.

- **Major bumps came back without a changelog citation** — the citation depended on the model calling the
  changelog tool, which small models often skip. The pipeline now fetches a breaking-change excerpt for each
  pending major bump itself, puts it in the LLM brief, and uses it as the citation when the model leaves it
  empty. The changelog tool also keeps major-release notes (`X.0.0`) ahead of newer minor releases, so the
  context budget no longer cuts off the release where the breaking changes are.

- **Migratowl's own log lines never appeared** — uvicorn configures only its own loggers, so `migratowl.*` INFO
  messages (pipeline candidates, validation results, sandbox lifecycle) were dropped and only warnings showed.
  New setting `MIGRATOWL_LOG_LEVEL` (default `INFO`); each line is printed once whether or not the root logger
  is configured. The pipeline summary now also counts update failures.

- **Python test runs missed test dependencies kept in PEP 735 groups** — validation only installed the
  `tests`/`test` extras, so projects that declare their test stack in `[dependency-groups]` (e.g. datasette)
  failed at collection and nothing was actually tested. Validation now also installs the `test`, `tests` and
  `dev` dependency groups when present (best effort, pip ≥ 25.1), before re-applying the bumped versions.

- **Releases the sandbox's Python cannot install were reported as breaking** — the runtime image ran Debian's
  Python 3.11, and the PyPI check ignored `requires_python`, so e.g. Sphinx 9.1 (Python ≥3.12) failed to
  install and was flagged as a breaking upgrade. The runtime image is now based on the official
  `python:3.13-slim-bookworm` image, and the PyPI check skips releases whose `requires_python` excludes the
  sandbox's Python (new setting `MIGRATOWL_SANDBOX_PYTHON_VERSION`, default `3.13`). Rebuild the runtime image.

- **A missing pytest made a dependency bump look breaking** — when a project declares pytest only in a dev
  group or tool config, the install step left it out of the venv and validation failed with
  `No module named pytest`. Validation now installs pytest when it is missing; if that fails, the test step is
  reported as skipped instead of failed.

- **`deepagents` had no upper bound** — `deepagents>=0.6` allowed 0.7, which removes the callable-backend
  API the agent factory and the package-analyzer subagent use, so a fresh `pip install` could pull a version
  Migratowl cannot run on. The range is now `>=0.6,<0.7` until the migration to the new backend API.

- **Resume leaked the old sandbox when it restarted a job** — when the surviving sandbox failed the
  liveness probe, `POST /jobs/{id}/resume` provisioned a new one and left the old SandboxClaim running
  until the idle-TTL sweep. The abandoned sandbox is now deleted (best effort).

- **Raw-mode sandboxes had no network on clusters that enforce NetworkPolicy** — with
  `MIGRATOWL_SANDBOX_BLOCK_NETWORK=true` (the default, and what the GitHub Action and GitLab
  component use with Calico), each sandbox pod gets a deny-all policy that also blocks DNS, so
  `git clone` and package installs failed. New `k8s/sandbox-egress-raw.yaml` re-opens DNS to kube-dns
  and HTTP/HTTPS to public addresses only; ingress, pod/service CIDRs, node networks and cloud metadata
  stay blocked. Apply it next to `k8s/rbac-raw.yaml`.
- **Sandbox commands broke on quotes and untrusted values** — `execute_project` and every
  `sh -c '…'` wrapper put the inner command inside single quotes, so an agent command such as
  `pip install -e '.[tests]'` (which the system prompt itself suggests) lost its quoting. Package
  names, versions, paths, the repo URL and the branch were also interpolated unquoted; several of these
  come from untrusted manifests. Every value is now passed as one shell argument, the `sh -c` script is
  quoted as a whole, and `git clone` takes the URL after `--`.

- **Python per-package runs were not isolated** — every working folder installed into the same
  site-packages, so the package-analyzer's single-package run tested against whatever `main/` had
  already upgraded. Each folder now gets its own venv (`<workspace>/.venvs/<folder>`), used by
  `update_dependencies`, `validate_project` and `execute_project`. Bumped versions are also re-applied
  after `pip install -e .`, which could otherwise downgrade them back inside the project's declared
  range (e.g. `requests<3`) and test the old version.
- **Raw mode's default image could not clone** — `MIGRATOWL_SANDBOX_IMAGE` defaulted to
  `python:3.12-slim`, which has no `git`, so a default raw-mode scan failed at `clone_repo`. The default
  is now `ghcr.io/bitkaio/migratowl-runtime:latest`, the image the GitHub Action and GitLab component
  already use.
- **Registry checks did not retry, and `Retry-After` had no limit** — `check_outdated` built its own
  HTTP client without the retry transport, so a 429 or 503 from PyPI, npm, crates.io, the Go proxy or
  Maven Central marked the package as a registry failure on the first try. It now uses the shared client
  (retries with backoff, `MIGRATOWL_HTTP_RETRY_*` settings). Each retry wait is capped at 60s, even when a
  server's `Retry-After` asks for longer. Outbound requests send `migratowl/<version>` as their
  User-Agent (registry calls said `migratowl/0.1.0`; everything else sent httpx's default).
  `migratowl.__version__` now exists and a test keeps it equal to `pyproject.toml`'s version.

- **Yanked PyPI releases could be reported as the latest version** — the PyPI check read every key in
  `releases` without looking at each file's `yanked` flag, so a withdrawn release could become the
  upgrade target. Releases whose files are all yanked are now skipped (crates.io already did this).

- **npm/crates prereleases picked as "latest" and rewritten** — every registry's versions were compared
  with Python's PEP 440 rules and returned in normalized form. npm's `rollup` `5.0.0-0` (a semver
  prerelease) was read as a post-release, chosen as latest and returned as `5.0.0.post0`, so
  `npm install` failed with `ETARGET` and the package was reported as breaking. npm, crates.io and Go
  versions are now compared as semver (`-x` is a prerelease that sorts before its release), and every
  registry's latest version keeps the spelling the registry published.
- **`requirements.txt` environment markers ended up in the version** — `foo==1.0; python_version < "3.8"`
  was stored with version `1.0; python_version < "3.8"`, and `foo ; python_version >= "3.8"` had the
  marker's `>=` read as its version operator. Markers are now stripped before parsing, as
  `pyproject.toml` parsing already did.
- **Dependencies outside the main sections were never scanned** — the manifest parsers now also read
  `[project.optional-dependencies]` and PEP 735 `[dependency-groups]`, Poetry
  `[tool.poetry.group.<name>.dependencies]`, Cargo `[build-dependencies]`, `[target.<cfg>.*]` tables
  and `[workspace.dependencies]`, and Gradle Kotlin DSL `build.gradle.kts` files. Malformed tables
  (a list or number where a table belongs) are skipped instead of raising.
- **The package-analyzer subagent contradicted the main agent's tool rules** — its prompt told it to
  use deepagents' `ls`/`read_file`/`grep`/`execute` (which the main prompt forbids and which don't work
  against the K8s sandbox), ran `execute_project` instead of `validate_project` (so it skipped the
  per-ecosystem build/test steps), described inputs it never receives, and allowed `null` in text
  fields `AnalysisReport` types as strings. It now follows the same rules as the main agent.
- **Token and cost totals were incomplete and partly wrong** — `total_input_tokens` /
  `total_output_tokens` only counted the main agent's messages, so package-analyzer subagent runs were
  missing. Usage is now collected from every model call (main agent and subagent). The report gains
  `total_cache_read_tokens` and `total_cache_creation_tokens`, and the PR comment's cost estimate prices
  cache reads and writes at their own rates. The price table now covers Claude Fable 5.1, Opus 5.5 and
  Sonnet 5.5, and fixes `claude-sonnet-5`, which was priced at $3/$15 instead of $2/$10 per 1M tokens.
- **PR notifications and callbacks followed different rules per outcome** — a commit status is now set
  whenever `commit_sha` is given (previously the start and final statuses also required `pr_number`), and a
  PR/MR comment is posted whenever `pr_number` is given — including a short failure notice with the error
  when the scan fails (previously failures only set a status). `callback_url` is now called on failure too,
  with `{job_id, state: "failed", error, repo_url, branch_name}`; the success body is unchanged. Both
  carry `X-Migratowl-Job-Id` and `X-Migratowl-Job-State` headers, and a non-2xx reply is logged as a
  warning. A failing comment no longer prevents the commit status from being set, or vice versa. The error text
  that reaches the PR comment, the callback and `GET /jobs` has URL credentials, GitHub/GitLab tokens
  and the configured `GITHUB_TOKEN`/`GITLAB_TOKEN` values redacted, and so does the server log. A
  `repo_url` with embedded credentials is masked (`https://***@host/...`) in reports, callbacks, the log and
  `GET /jobs` responses; the stored payload keeps the real URL so resume can still clone.
- **Shutdown left queued jobs and scan tasks behind** — on shutdown only `running` jobs were marked
  `interrupted`; jobs still queued behind the scan semaphore stayed `pending` until the next boot, and
  in-flight scan tasks were not cancelled. Shutdown now cancels in-flight scans (their sandbox and
  checkpoint are kept for resume) and marks both running and queued jobs `interrupted`.
- **Runtime image could not build Java projects** — `k8s/runtime/Dockerfile` had no JDK, Maven or
  Gradle, so `validate_project` failed on every Java repo. The image now ships Eclipse Temurin 21,
  Maven 3.9.16 and Gradle 9.8.0. Go moves from the end-of-life 1.23.6 to 1.27.1, and every downloaded
  archive (Go, JDK, Maven, Gradle) is checksum-verified before it is unpacked. The image also no longer
  builds against a stale checksum: `https://sh.rustup.rs` serves a new script on every rustup release,
  so the image now installs a pinned `rustup-init` 1.29.1 binary instead.

- **Parser crashes on malformed manifests/changelogs** (found by fuzzing, [#12](https://github.com/bitkaio/migratowl/issues/12)) —
  `parse_package_json` no longer crashes on a non-object JSON root (e.g. a bare `5` or `"str"`) or on
  non-dict dependency sections / non-string version values; it returns `[]` or coerces safely.
  `filter_chunks_by_version_range` no longer raises `TypeError` when a changelog has a malformed version
  heading (e.g. `1.-1`) alongside valid version bounds — incomparable chunks are skipped.

- **`skipped` list no longer includes non-outdated dependencies** — previously the LLM agent could
  incorrectly include up-to-date dependencies (e.g. `@popperjs/core`) in the `skipped` field of
  `ScanAnalysisReport`. The skipped list is now computed deterministically in code: only outdated
  dependencies that weren't analyzed (due to `max_deps` limit) appear in `skipped`. Non-outdated
  dependencies are never candidates for analysis and correctly don't appear anywhere except
  `scan_result.all_deps`.

- **One slow sandbox command no longer fails the whole scan** — a command that ran past its timeout
  (raw mode `TimeoutError`, or the sandbox-router giving up in agent-sandbox mode) escaped the agent and
  ended the job as `"Internal scan error"`. It now comes back to the agent as a failed command
  (exit code `124`) so it can retry with a narrower command or a larger timeout.

- **Sandbox-router cut off commands after 180s** — the vendored router (`k8s/sandbox-router/`)
  hard-coded a 180s proxy timeout, so long installs and test runs failed with a 500. It now reads
  `PROXY_TIMEOUT_SECONDS`, which `k8s/sandbox-router.yaml` sets to `1800`, and returns `504` on timeout.
  Rebuild the router image and re-apply the manifest to pick it up.

- **Sandbox pods and SandboxClaims leaked** — a finished job's sandbox stayed up until shutdown or the
  TTL sweep; it is now deleted as soon as the job completes or fails (interrupted jobs keep theirs for
  resume). Separately, `MIGRATOWL_SANDBOX_KUBE_API_URL` was never read, so in agent-sandbox mode the
  startup TTL sweep and shutdown cleanup always failed off-cluster. New settings
  `MIGRATOWL_SANDBOX_KUBE_API_URL` and `MIGRATOWL_SANDBOX_KUBE_TOKEN` are now passed through; for
  local development run `kubectl proxy` and set the URL to `http://localhost:8001`.

- **Scans with no report no longer pass as "nothing outdated"** — when the agent finished without
  returning a structured report (seen with weaker models that wrote the report to a file instead), the
  job ended `completed` with an empty report, indistinguishable from a clean dependency scan. It now
  ends `failed` with the error `Agent finished without returning a structured report`, and the PR gets
  the failure notice instead of a misleading success comment.

## [0.6.0] - 2026-07-16

### Changed

- **Default LLM model bumped to `claude-sonnet-5`** — successor to `claude-sonnet-4-6`; near-Opus quality
  on agentic/coding work at the same $3/$15 per-MTok sticker price (introductory $2/$10 through
  2026-08-31). Note: Sonnet 5 uses a new tokenizer that produces ~30% more tokens for the same text, so
  per-scan token counts in the cost-telemetry footer will read higher even though pricing is unchanged.
- **`deepagents` upgraded from 0.4.11 to 0.6.12** — brings code interpreter middleware, `stream_events`
  v3, and delta-channel checkpointer storage (10-100x smaller). `migratowl/patches.py`'s
  `_patch_subagent_recursion_limit` was updated: deepagents 0.6's `_build_task_tool()` now takes
  keyword-only `private_state_keys` / `state_schema` params and accepts a mix of raw `SubAgent` dict
  specs (no `runnable` key, compiled internally) alongside `CompiledSubAgent` specs — the patch now
  forwards all arguments and only wraps specs that already have a `runnable`.
- **agent-sandbox controller updated to v0.2.1** (from v0.1.0) — the controller moved from a
  StatefulSet to a Deployment (delete the old StatefulSet before upgrading an existing cluster), and
  SandboxTemplates without an explicit network policy now default to a strict "secure by default"
  isolation posture (blocks internal cluster IPs, VPC subnets, and the node metadata server). See
  `dev-setup.md` for the upgrade note.

### Security

- **14 known vulnerabilities across 6 packages resolved** — `pip-audit` in CI began failing as new
  2026 advisories landed against an aging lockfile (the pins predated this release; a plain `uv lock`
  is conservative and never moved them). Resolved with **targeted** `uv lock --upgrade-package` rather
  than a blanket `--upgrade`, so the agent framework (`langchain`, `langgraph`, `langchain-anthropic`,
  `deepagents`) stays on its validated versions:
  `cryptography` 46.0.7 → 49.0.0 (GHSA-537c-gmf6-5ccf), `idna` 3.11 → 3.18 (PYSEC-2026-215),
  `urllib3` 2.6.3 → 2.7.0 (PYSEC-2026-141/142), `pydantic-settings` 2.13.1 → 2.14.2
  (GHSA-4xgf-cpjx-pc3j), `python-multipart` 0.0.26 → 0.0.32 (PYSEC-2026-3036/3037/3039/3040), and
  `starlette` 0.52.1 → 1.3.1 (PYSEC-2026-161/248/249/2280/2281). The starlette 0.x → 1.x major bump
  pulled `fastapi` 0.135.1 → 0.139.2; neither `fastapi` (`starlette>=0.46.0`) nor `sse-starlette`
  (`starlette>=0.49.1`) caps the major, and the full HTTP surface was re-verified under 1.3.1.
- **Sandbox runtime image dependencies patched** — `k8s/runtime/requirements.lock.txt` carried the
  same vulnerable `starlette` (1.0.0), `python-multipart` (0.0.26), and `idna` (3.11). Regenerated to
  `starlette` 1.3.1, `python-multipart` 0.0.32, `idna` 3.18, `fastapi` 0.139.2. Note this lockfile is
  **not** covered by the CI `pip-audit` step, which only audits `uv export` of the top-level lock.
- **Dependency floors raised to prevent regression** — `pydantic-settings>=2.14.2` and
  `python-multipart>=0.0.31` (in `pyproject.toml` and `k8s/runtime/requirements.txt`), so a fresh
  resolve cannot pick a known-vulnerable version. `cryptography>=48.0.1` is enforced via
  `[tool.uv] constraint-dependencies`: it is transitive (via `google-auth`/`kubernetes`) and its
  dependents declare only open lower bounds, so the resolver otherwise kept the vulnerable 46.0.7.
- **`k8s-agent-sandbox` capped to `<0.3`** — `langchain-kubernetes` 0.4.0 declares
  `k8s-agent-sandbox>=0.1.0` with no upper bound, but the client API changed in 0.3+
  (`SandboxClient.__init__` dropped `template_name` for `connection_config`). Resolving to 0.5.x fails
  at runtime with `TypeError: ... unexpected keyword argument 'template_name'`. **The unit suite does
  not catch this** — the sandbox backend is mocked — so it only surfaces in an end-to-end scan. Lift
  the cap once `langchain-kubernetes` supports the newer client.

### Added

- **CI now audits the sandbox runtime lockfile** — a second `pip-audit` step covers
  `k8s/runtime/requirements.lock.txt`. The existing step only exports the top-level `uv.lock`, so the
  runtime image's separate dependency set shipped unscanned (and was carrying three of the CVEs above).

### Fixed

- **LLM cost-telemetry pricing table corrected** — `claude-opus-4-7` was priced at $15/$75 per MTok
  (Opus 4.5-era pricing); the correct rate is $5/$25 (Opus 4.6+ pricing). `claude-haiku-4-5-20251001`
  was priced at $0.80/$4; the correct rate is $1/$5. Added entries for `claude-sonnet-5` ($3/$15),
  `claude-opus-4-8` ($5/$25), `claude-fable-5` ($10/$50), and a bare `claude-haiku-4-5` alias.

## [0.5.0] - 2026-05-06

### Added

- **LLM cost telemetry in PR comments** — the PR/MR comment footer now shows token usage and an
  estimated cost for each scan. Example: `1.2M tokens (↑890K / ↓355K) · ~$0.12`. Token counts
  are accumulated from `AIMessage.usage_metadata` across all agent turns. Cost estimates are
  computed from a built-in pricing table covering `claude-sonnet-4-6`, `claude-opus-4-7`,
  `claude-haiku-4-5-20251001`, `gpt-4o`, and `gpt-4o-mini`; unknown models display token counts
  without a cost estimate.
- **Registry query failures surfaced in PR comments** — packages that could not be queried (e.g.
  network errors, unsupported registries) are now collected into a `RegistryFailure` list and
  shown in a collapsible `<details>` block at the bottom of the PR comment
  ("N package(s) could not be queried"). Previously these failures were silently swallowed,
  causing packages to disappear from the output without any indication. `ScanResult` now carries
  a `registry_failures` field; `check_outdated` returns
  `tuple[list[OutdatedDependency], list[RegistryFailure]]`.

### Changed

- **Fix details moved to a collapsible block** — `suggested_human_fix` for breaking packages is
  no longer truncated at 120 characters in the PR comment table cell. The table cell now shows
  the first sentence (or ≤ 80 characters) as a short summary; the full fix text appears in a
  collapsible "Fix details (N package(s))" `<details>` block below the table.
- **Changelog fetched for major-version bumps when tests pass** — when `validate_project` passes
  but a package has a major-version bump (`current_major < latest_major`), the agent now calls
  `fetch_changelog_tool` to inspect for breaking changes and sets `is_breaking`, `confidence`
  (0.9), and `suggested_human_fix` from the changelog. Previously the agent marked all packages
  `is_breaking=false, confidence=1.0` whenever tests passed, silently skipping changelog
  inspection for major bumps. The same logic applies to the `package-analyzer` subagent.

## [0.4.0] - 2026-04-25

### Added

- **Zero-config CI distribution** — two new companion repositories make Migratowl installable in
  under two minutes with no Kubernetes infrastructure:
  - **`bitkaio/migratowl-action`** — GitHub Composite Action published to the GitHub Marketplace.
    Spins up an ephemeral [kind](https://kind.sigs.k8s.io/) cluster with Calico CNI inside the
    runner, runs the scan in raw sandbox mode, and tears everything down on completion. Supports
    Dependabot/Renovate PR targeted scans, scheduled full-repo scans, and three
    `results-destination` options (`pr-comment`, `issue`, `artifact`).
  - **`bitkaio/migratowl-gitlab-component`** — GitLab CI/CD Component published to the Component
    Catalog. Mirrors the Action's behaviour using `docker:dind`; all seven steps are inlined in
    `templates/scan.yml`. Supports self-managed GitLab via the `gitlab-api-url` input.
- **Prebuilt multi-arch runtime image** — `ghcr.io/bitkaio/migratowl-runtime:<version>` is now
  built and pushed to GHCR on every release (`linux/amd64` + `linux/arm64`). The Action and
  Component pull this image instead of rebuilding `k8s/runtime/` on every CI run, saving 1–3
  minutes per scan.
- **Getting Started section in README** — two named paths presented side by side: *Zero-config*
  (no cluster needed, links to the Action and Component) and *Self-hosted* (existing cluster,
  links to the renamed Quick Start section).

### Changed

- **PR comment no longer shows a confidence column** — the `Confidence` column has been removed
  from the PR/MR comment table. The table is now `| Package | Status | Fix |`. Confidence scoring
  is still computed internally but is not surfaced until it is validated against real-world repos.
- **`docs/examples/ci-only.yml`** — added `statuses: write` permission (required to post commit
  statuses); improved scan result display to guard against null `outdated` / `reports` fields;
  logs the Migratowl server output when the scan state is `failed`.
- **License updated to Apache 2.0** — all source files and README now carry Apache 2.0 headers
  and SPDX identifiers (`SPDX-License-Identifier: Apache-2.0`).

## [0.3.0] - 2026-04-18

### Added

- **Raw sandbox mode** (`MIGRATOWL_SANDBOX_MODE=raw`) — runs on any Kubernetes cluster without
  the agent-sandbox controller or CRDs. Migratowl manages ephemeral pods directly via the
  Kubernetes API. Intended for CI environments (kind, EKS, GKE) where CRD installation is not
  practical. Controlled by three new settings: `MIGRATOWL_SANDBOX_MODE`, `MIGRATOWL_SANDBOX_IMAGE`
  (default `python:3.12-slim`), and `MIGRATOWL_SANDBOX_BLOCK_NETWORK` (default `true`; note that
  kind's default CNI kindnet does not enforce NetworkPolicy).
- **`k8s/rbac-raw.yaml`** — RBAC manifest for raw mode; grants direct Pod create/delete and
  NetworkPolicy create/delete instead of the Sandbox CR verbs used by agent-sandbox mode.
- **Migratowl self-scan workflow** (`.github/workflows/migratowl-scan.yml`) — Migratowl scans its
  own dependencies on `workflow_dispatch` (full scan) and on Dependabot PRs (targeted single-dep
  scan). Spins up a kind cluster in raw mode, starts an ephemeral Migratowl server, and polls for
  completion with a 5-minute initial wait and a 1-hour wall-clock deadline.
- **`docs/examples/ci-only.yml`** — drop-in GitHub Actions workflow for projects that want
  Migratowl scans without running a persistent server; self-contained, requires only
  `ANTHROPIC_API_KEY` as a repository secret.
- **`docs/examples/with-migratowl-server.yml`** — renamed from `dependabot-scan.yml`; triggers an
  existing Migratowl deployment via webhook.

### Changed

- `docs/examples/dependabot-scan.yml` renamed to `docs/examples/with-migratowl-server.yml` for
  clarity.

## [0.2.0] - 2026-04-09

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
  when checking for outdated dependencies. `"normal"` (default) ignores the constraint operator
  and compares the bare version against the globally highest published version, surfacing
  major-version bumps such as `express 4.x → 5.x` that the registry's own `latest` tag would
  otherwise hide. `"safe"` respects the declared semver constraint (e.g. `^4.21.2` only flags a
  newer version if one exists within `>=4.21.2,<5.0.0`).
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

- **`check_deps` field on `POST /webhook`** — allowlist counterpart to `exclude_deps`. When
  non-empty, only the listed dependency names are checked; all other dependencies are ignored.
  Defaults to `[]` (check everything). Useful for targeted scans when only a specific subset of
  dependencies is of interest.

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

[Unreleased]: https://github.com/bitkaio/migratowl/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/bitkaio/migratowl/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/bitkaio/migratowl/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/bitkaio/migratowl/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/bitkaio/migratowl/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/bitkaio/migratowl/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/bitkaio/migratowl/releases/tag/v0.1.0
