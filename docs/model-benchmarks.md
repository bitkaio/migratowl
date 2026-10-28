# Model Benchmarks

What a scan costs, how long it takes and how often its verdicts are right, by model. Measured on 2026-10-12 with Migratowl 0.8.2 and a fix for cache-write counting that ships in the next release. The repositories are small test projects with known answers, so read the numbers as an example of relative cost and quality, not as a quote for your repository.

## Summary

| Model | Correct verdicts | Spurious 🔍 Review flags | Cost of the three scans | Python scan time |
|---|---|---|---|---|
| `claude-sonnet-5-5` (default) | 13 / 13 | 1 | $0.16 | 64 s |
| `claude-haiku-5-5` | 13 / 13 | 0 | $0.014 | 72 s |
| `nvidia/nemotron-3-super-120b-a12b:free` (OpenRouter) | 12–13 / 13 | about 2 | $0 | 3–6 min |

- **Sonnet 5.5** writes the most precise reasons ("`engine.execute("select 42")` at app/db.py:6; `Engine.execute()` was removed in SQLAlchemy 2.0") and the fewest output tokens.
- **Haiku 5.5** matched Sonnet on these projects at under a tenth of the cost. It is a good choice for frequent scans.
- **The free model** is fine for trying Migratowl out. It gives low or zero confidence on safe verdicts and often cites nothing, which raises Review flags on packages that are fine. In one run it called the SQLAlchemy 2 upgrade safe; the code evidence still marked it 🔍 Review, so it was not reported as plainly safe.

## Per scan

| Repository | Model | Time | Model calls (agent / subagents) | Input tokens (cache read / cache write) | Output tokens | Cost |
|---|---|---|---|---|---|---|
| Python | Sonnet 5.5 | 64 s | 3 / 9 | 171,822 (136,002 / 35,790) | 2,836 | $0.131 |
| JavaScript | Sonnet 5.5 | 62 s | 2 / 0 | 29,435 (26,819 / 2,610) | 1,203 | $0.021 |
| Java | Sonnet 5.5 | 25 s | 2 / 0 | 25,835 (25,008 / 821) | 331 | $0.008 |
| Python | Haiku 5.5 | 72 s | 3 / 9 | 192,824 (143,135 / 49,659) | 8,242 | $0.012 |
| JavaScript | Haiku 5.5 | 59 s | 2 / 0 | 30,374 (26,819 / 3,549) | 2,647 | $0.002 |
| Java | Haiku 5.5 | 27 s | 2 / 0 | 25,691 (25,008 / 677) | 612 | $0.001 |
| Python | free model | 161–348 s | 4–7 / 7–14 | 116,268–214,899 | 14,195–30,472 | $0 |
| JavaScript | free model | 154 s | 4 / 0 | 40,022 | 9,560 | $0 |

Times are for the analysis; the GitHub Action and the GitLab component add 3–4 minutes for the cluster and the server.

## What the call budget is worth

The same Python scan on the free model without call limits (`MIGRATOWL_MAX_MODEL_CALLS=0`, `MIGRATOWL_MAX_SUBAGENT_MODEL_CALLS=0`, `MIGRATOWL_CONTEXT_TRIM_TOKENS=0`):

- **Usage:** 113 model calls, 2.05 million input tokens and 11 minutes, for the same verdicts.
- **Priced at Sonnet 5.5 rates:** about $1.25, against about $0.13 with the default budget (the $1.25 was priced with the earlier $0.20 cache-read rate).

## The test repositories

| Repository | Upgrade | Known answer |
|---|---|---|
| [`barnakun/migratowl-fixture-python`](https://github.com/barnakun/migratowl-fixture-python) | pydantic 1.10 → 2.14 | breaking: `from pydantic import BaseSettings` |
| | SQLAlchemy 1.4 → 2.1 | breaking: `engine.execute(...)` |
| | click 7 → 8 | safe: only `@click.command` and `click.echo`, in untested code |
| | requests 2.31 → 2.34 | safe (minor) |
| [`barnakun/server-side-js`](https://github.com/barnakun/server-side-js) | express 4 → 5 | breaking: `'/?search=:query'` route paths |
| | ejs 3 → 7, jest 29 → 30 | safe for this code |
| | body-parser, bootstrap, express-session, mongoose | safe (minor) |
| [`barnakun/migratowl-fixture-java`](https://github.com/barnakun/migratowl-fixture-java) | guava 31 → 33 | safe: only `ImmutableList` |
| | commons-lang3 3.12 → 3.21 | safe (minor) |

## How cost is computed

Cost is computed from the report's token counts at Anthropic's list prices per million tokens:

| Model | Input | Output | Cache read | Cache write (5 min) |
|---|---|---|---|---|
| Sonnet 5.5 | $2.00 | $10.00 | $0.10 | $2.50 |
| Haiku 5.5 | $0.10 | $0.50 | $0.01 | $0.125 |

Haiku 5.5 bills a request whose prompt (cache reads and writes included) is over 100K tokens at higher prices ($0.50 input, $2.50 output, $0.05 cache read, $0.625 cache write); the scans above stay under that. The PR comment's cost line uses the lower Haiku prices, because it only sees the scan's totals, so on very large repositories it can read low. Sonnet 5.5 cache reads have cost $0.10 since 2026-10-07 (they were $0.20). Prices change: check Anthropic's pricing page before relying on these numbers.

## Reproducing

1. Run a local cluster with the agent-sandbox controller ([Kubernetes Setup](../README.md#kubernetes-setup)) and start the server with the model to test: `MIGRATOWL_MODEL_NAME=claude-haiku-5-5 uv run uvicorn migratowl.api.main:app`.
2. Scan each repository with `POST /webhook` and `{"repo_url": "<repository>"}`, and poll `GET /jobs/{id}`.
3. Read the result's `reports`, `reviews`, `total_*_tokens` and `model_calls`, and compare the verdicts with the table above.
