# InsightOS

**From business questions to autonomous decisions.**

An autonomous business intelligence platform. You ask a question in plain
English; the system resolves it against a governed metric layer, generates and
validates read-only SQL, decomposes what changed, tests whether the change is
real, forecasts forward, verifies its own claims, and recommends what to do —
showing its working at every step.

---

## Status: working product, not a complete spec

This repository implements the **spine** of the product described in the build
specification, end to end and working, plus persistence, causal inference,
alerting, simulation and an evaluation harness. It does not implement all 82
sections of that spec — that is a multi-quarter programme for a team.

Per the spec's own rule ("if a feature is not implemented yet, create a clear
TODO rather than pretending it works"), here is the honest split.

### Built and working

| Capability | Where | Verified by |
|---|---|---|
| SQL validation & injection defence | `app/sql_engine/validator.py` | 16 security tests |
| Semantic layer with metric governance | `app/semantic/registry.py` | 4 tests |
| Root-cause / contribution attribution | `app/analytics/rootcause.py` | 5 tests, exact reconciliation |
| Anomaly detection (3 detectors, auto-selected) | `app/analytics/anomaly.py` | 2 tests |
| Forecasting with rolling-origin backtest | `app/analytics/forecast.py` | 5 tests |
| Statistical significance testing | `app/analytics/significance.py` | used throughout |
| Data profiling & quality scoring | `app/analytics/profiling.py` | integrated |
| Confidence decomposition | `app/analytics/confidence.py` | 3 tests |
| Critic / verification agent | `app/agents/critic.py` | 6 tests |
| Investigation orchestrator (14 stages, streaming) | `app/agents/orchestrator.py` | 7 end-to-end tests |
| **Causal inference (diff-in-diff + diagnostics)** | `app/analytics/causal.py` | 11 tests |
| **Scenario simulation + sensitivity + break-even** | `app/analytics/simulation.py` | 13 tests |
| **Alert compiler, engine and backtest** | `app/alerts/` | 21 tests |
| **Background alert sweep (arq)** | `app/workers/alerts.py` | 6 tests |
| **Evaluation harness with CI thresholds** | `app/evaluation/` | 6 tests + 6 golden cases |
| **Persistence, repositories, tenant scoping** | `app/db/`, `app/repositories/` | 15 tests |
| **Auth: signup, login, token rotation, reuse detection** | `app/api/v1/auth.py` | 22 integration tests |
| **Alembic migrations + CI drift check** | `migrations/` | 3 tests |
| **Rate limiting (Redis + in-memory fallback)** | `app/core/ratelimit.py` | ✓ |
| LLM gateway + model router + graceful degradation | `app/llm/gateway.py` | runs with no API key |
| Database schema (16 tables, tenant-scoped) | `app/db/models.py` | ✓ |
| **Data sources: crawl, secrets, read-only execution** | `app/datasources/`, `app/sql_engine/executor.py` | 38 tests |
| **SQL generation from governed metrics** | `app/datasources/provider.py` | 13 tests |
| **Text-to-SQL with validator-gated repair loop** | `app/sql_engine/generator.py` | 24 tests |
| **Notification delivery (webhook/email/log)** | `app/notifications/` | 20 tests |
| **AutoML: baselines, calibration, leakage detection** | `app/ml/automl.py` | 25 tests |
| **Report export (Markdown + PDF)** | `app/reports/export.py` | 16 tests |
| **RAG: chunking, hybrid retrieval, citations** | `app/rag/` | 43 tests |
| **Prompt-injection defence (3 layers)** | `app/rag/injection.py`, `app/agents/critic.py` | ✓ |
| Synthetic warehouse generator | `scripts/seed_data.py` | ~170k rows |
| Eight frontend routes | `apps/web/` | typecheck + build clean |

**359 tests pass. The evaluation harness passes all six thresholds.**

### Not started

Knowledge graph, Kafka streaming, and OpenTelemetry wiring. The first two are
argued against below; the third is straightforward wiring rather than design.

`GET /api/v1/capabilities` returns this split at runtime, so the UI renders an
honest state rather than advertising features that are not wired up.

### The largest remaining gap

Text-to-SQL. Every query is generated from a governed metric definition, which
is correct by construction and auditable — but it means a question the semantic
layer has no metric for cannot be answered at all. The validator is strong
enough to gate generated SQL (it has caught two bypass attempts under test) and
the crawler now supplies the schema context generation needs, so this is the
next thing worth building.

---

## The core design decision

> **Python computes every number. The LLM plans and narrates. The critic checks
> the narrative against the numbers before anyone sees it.**

This is what makes the output auditable, and it is why the system still works
with no API key configured — you get the full investigation with a templated
narrative instead of a written one.

The critic parses every numeric claim out of the generated prose and requires
each one to match a value that Python actually computed. A fluent, wrong answer
fails `numbers_grounded` and the conclusion is suppressed.

---

## Quick start

```bash
git clone <this-repo> && cd insight-os
cp .env.example .env                     # works as-is; no API key needed

# 1. Generate the demo warehouse (~160k rows, ~90s)
pip install pandas numpy
python scripts/seed_data.py --out ./seed

# 2. Start the stack
docker compose up --build

# 3. Open http://localhost:3000/investigate
```

### Put it on GitHub

```bash
gh repo create insight-os --private --source=. --push
```

or, without the `gh` CLI, create an empty repo on github.com and:

```bash
git remote add origin git@github.com:<you>/insight-os.git
git push -u origin main
```

Nothing further is needed: CI runs on push with no secrets configured, because
the whole system runs offline. Four jobs — `api` (lint, migrations, 359 tests,
migration-drift check, evaluation harness), `web` (typecheck, lint, build),
`smoke` (starts the real server and drives 40 checks over HTTP), and `security`
(scans tracked files for credential patterns).

GitHub Actions runs the checks; it does not host the app. For that, `docker
compose up` gives you the full stack, or point any container host at
`apps/api/Dockerfile` and `apps/web/Dockerfile`.

### Prove it works

```bash
./scripts/run_local.sh
```

Generates the demo warehouse if it is missing, loads it into SQLite so the data
source path runs against a real database rather than CSVs, migrates, starts the
API, and drives 39 end-to-end checks over HTTP — signup through token rotation,
investigation, export, causal estimation, alert backtesting, schema crawl,
model training, retrieval, injection defence and tenant isolation. Non-zero exit
if any step fails.

A suite of unit tests cannot prove the pieces are wired together: each one
constructs its own objects, and wiring is exactly what they cannot check.

Or run the API alone:

```bash
cd apps/api
pip install -e ".[dev]"
alembic upgrade head                     # or let ENV=local bootstrap the schema
pytest -q                                # 359 tests
python -m app.evaluation.harness         # quality gate, exits non-zero on regression
uvicorn app.main:app --reload
```

---

## The demo dataset is not staged

`scripts/seed_data.py` simulates a business with **latent causal mechanisms**,
not labelled outcomes. There is no `revenue_drop` column. Instead:

1. A support-quality regression begins in one region on a configurable date.
2. Ticket volume and first-response time degrade there.
3. Enterprise accounts in that region contract their spend, then churn, on a
   realistic 3–6 week lag.
4. Revenue falls as a *consequence*.

Nothing downstream is told any of this. The root-cause engine has to find it by
decomposing the delta. On the default seed it independently ranks that region
first at **−19.2% within-segment**, having been given no hint where to look.

Change `--seed` or `--incident-start` and every headline number changes, because
nothing is hardcoded.

---

## What an investigation actually does

```
Understanding the question       ✓  0.02s
Building an analysis plan        ✓  0.03s
Resolving the governed metric    ✓  0.03s
Generating SQL                   ✓  0.04s
Validating SQL                   ✓  0.04s
Executing query                  ✓  0.31s
Profiling the result set         ✓  0.44s
Detecting anomalies              ✓  0.46s
Investigating root causes        ✓  0.52s
Testing significance             ✓  0.53s
Forecasting forward              ✓  1.87s
Drafting recommendations         ✓  1.88s
Verifying against evidence       ✓  1.89s
Investigation complete           ✓  1.89s
```

A real result from the seeded warehouse:

```
CONFIDENCE: 32% (insufficient)   limiting factor: statistical
   data          75.2%
   statistical    4.1%    ← p=0.90, the aggregate change is within normal variation
   model         63.1%
   reasoning    100.0%
```

The system is *supposed* to say this. Overall revenue moved 0.88% month over
month, which is noise — but one region moved −19.2%, which is not. Reporting
high confidence in the headline would have been the wrong answer.

---

## Two questions root-cause analysis cannot answer

**"Did it *cause* the drop?"** — `POST /api/v1/analysis/causal/diff-in-diff`
runs difference-in-differences with cluster-robust inference, gated by a
parallel-trends pre-test and a placebo test. On the demo warehouse it finds the
seeded region at −58% with both diagnostics passing, and **refuses** to call two
other regions causal despite large point estimates. A failed diagnostic returns
`credible: false` and the UI strikes the number through rather than footnoting
it.

**"What if we fixed it?"** — `POST /api/v1/analysis/simulate` propagates stated
assumptions through the segment decomposition and returns a sensitivity tornado
alongside the answer. Every lever declares whether its magnitude was asserted or
derived from data, and asserted levers are labelled as such in the output, so a
guess is never laundered into an estimate.

---

## Connecting a real warehouse

The demo CSVs are one implementation of a narrow interface. Everything
downstream — orchestrator, alert engine, causal estimator — takes a
`(metric, start, end) -> DataFrame` callable and cannot tell the difference.

```bash
export INSIGHTOS_SECRET_WAREHOUSE='postgresql+asyncpg://reader:...@host/db'
```

```
POST /api/v1/datasources          {"name": "Warehouse", "kind": "postgres",
                                   "secret_ref": "warehouse"}
POST /api/v1/datasources/{id}/crawl
GET  /api/v1/datasources/{id}/schema
```

Three properties worth naming:

**The credential is never stored.** `secret_ref` holds a pointer; a pasted DSN
is rejected at the API boundary with a message saying so, because a connection
string in the database ends up in backups and in `SELECT *` during debugging.

**The catalog is derived from the crawl, never hand-written.** A hand-maintained
allowlist drifts in the direction that matters — it goes stale *permissive*. A
table dropped upstream disappears from the catalog on the next crawl.

**Read-only is enforced three ways, independently.** The validator is a parser
and parsers have bugs, so a write that slips past the AST allowlist still hits a
read-only transaction, and a query that plans badly still hits a statement
timeout and a row cap. `test_writes_are_blocked_at_the_connection_too`
deliberately stubs out the validator to prove the second layer works alone.

Where a backend enforces less, it says so rather than being assumed safe: SQLite
returns a warning that it has no statement timeout.

---

## Asking a question with no metric behind it

The semantic layer answers governed questions correctly by construction. For
everything else there is `POST /api/v1/query/ask`, and it is the most dangerous
endpoint in the product — so it is the most constrained.

**The model proposes; the validator disposes.** Generated SQL goes through the
same AST allowlist, catalog check and LIMIT injection as anything hand-written,
then executes in a read-only transaction. It is never run because it looked
plausible.

**Rejections become repair prompts.** The validator's findings are already
phrased as instructions — *"Column 'regionn' does not exist on any referenced
table. Did you mean 'region'?"* — so they make good input. A model given the
specific rule it broke, plus its own rejected query verbatim, fixes it; a model
told only that something was wrong rewrites from scratch and makes a different
mistake. Capped at three attempts, all of them returned.

**With no model configured it declines.** A fabricated query that happens to
parse is worse than a refusal, because the first one returns numbers.

`POST /api/v1/query/explain` generates and validates *without* executing, because
the right first response to SQL a model wrote is to read it.

Answers from here are not equivalent to metric-backed ones and the response says
so: a metric has an owner, an approval and a version, and ad-hoc SQL has a
model's best guess at what you meant.

---

## Training a model on your own data

`POST /api/v1/models/train` fits three candidates and tells you whether any of
them is worth using. Most of the module is that second part.

**A temporal split by default.** Business data is time-ordered, and a random
split trains on next month to predict last month. It is the single most common
reason a churn model scores 0.94 in a notebook and fails in production.

**A baseline the model has to beat by more than the noise.** Predicting the
majority class scores 89% accuracy on an 11% churn rate. The first version of
the gate used a flat 0.02 AUC margin and passed pure noise as a working model —
on 500 held-out rows chance AUC varies by about ±0.04 on its own. The margin is
now two standard errors of the AUC, and a four-seed test holds it honest.

**Calibration, not just ranking.** On the demo data the AUC winner is a
class-weighted logistic regression whose Brier score is *worse than predicting
the base rate*. It ranks well and its probabilities are unusable — fine for a
leaderboard, wrong for anything that multiplies a probability by a revenue
figure. Selection now prefers the calibrated model and explains the swap.

**Leakage detection.** A feature that alone predicts the outcome at AUC ≥ 0.98
is nearly always populated *because* the outcome happened. The run flags it and
marks the model unusable rather than reporting the score.

---

## Taking a report out of the tool

`GET /api/v1/investigations/{ref}/export?format=markdown|pdf`.

One rule: **nothing is exported without its qualifiers.** Confidence carries its
limiting factor and an explanation of what that factor means. Drivers carry both
contribution and own change, because a large percentage on a small segment moves
the total very little. A causal estimate that failed its diagnostics is withheld,
not footnoted. Expected impact carries the assumption behind it, or it reads as
a forecast instead of arithmetic.

An export that renders only the headline is easy to build and actively harmful:
it strips exactly the parts that tell a reader how much weight the number bears.

---

## Answering from documents

`POST /api/v1/documents/ask` grounds an answer in text somebody wrote rather
than in computed figures, which changes what the system can promise. A warehouse
number is reproducible; a passage is a quotation, so the answer is the quotation
plus where it came from. An answer that cannot cite is not returned.

**Retrieval fuses two lexical signals.** BM25 over stemmed tokens does the work;
character n-grams catch what BM25 misses silently — a typo, a plural, or
"authorisation" against a document spelling it with a z. They are fused by
reciprocal rank because a BM25 score of 8 and a cosine of 0.4 cannot be added
without inventing weights. A dense embedding slot exists and is empty, and the
index *says so* rather than reporting a similarity computed from nothing.

**Scores are reported as ranks, never percentages.** A fused RRF score has no
reading as a probability, and rendering it as "87% relevant" would invent a
confidence the method cannot support.

---

## Retrieved text is data, never instruction

RAG is the first feature that puts text somebody else wrote into a model's
context. An uploaded document is an untrusted input that happens to look like a
source, so the defence has three layers and needs all three:

1. **Detection** at both ingestion and retrieval. Instruction-shaped content is
   scored with its matches attached, because a security signal a reviewer cannot
   audit is one they learn to ignore. The uploader is warned at upload — they
   are the person who can say whether the file should contain that text.
2. **Delimiting and annotation.** Passages are wrapped in an explicit data
   boundary. Suspicious lines are annotated rather than deleted: removing them
   would make any quotation of the document wrong and hide the attack from
   whoever reviews the answer.
3. **The critic.** A narrative that adopts directive content found in a
   retrieved passage is blocked before release. This is the layer that does not
   depend on recognising the phrasing — an attacker who knows the patterns
   writes around them, so the last check looks at the *outcome* instead.

The success case is tested explicitly: a document containing an attack, an
answer that ignored it, and a critic that lets it through.

---

## The evaluation harness is a gate

```bash
python -m app.evaluation.harness     # runs in CI; non-zero exit fails the build
```

Six golden cases whose ground truth is known by construction. Two of them
contain nothing to find — because a system scored only on cases with a real
answer learns to always produce one, **restraint** is scored separately from
recall.

It has already caught four real defects: degenerate dimensions outranking the
true driver, unqualified columns bypassing the SQL validator, partial comparison
windows manufacturing changes out of missing days, and calendar-edge periods
poisoning trend estimation.

---

## Security posture

The SQL validator is an allowlist over the parsed AST (`sqlglot`), never regex.
Verified blocked in `tests/test_sql_security.py`:

- stacked statements (`...; DROP TABLE ...`)
- every write and DDL statement
- system catalogs (`pg_catalog`, `information_schema`, `sqlite_master`)
- filesystem and network functions (`pg_read_file`, `dblink`, `lo_import`, …)
- hallucinated tables and columns — fail closed, with a nearest-match hint.
  Unqualified columns are resolved against the union of referenced tables, so
  `SELECT nonexistent_column FROM orders` is rejected too
- cartesian joins, ungrouped aggregates, excessive nesting and join counts

`LIMIT` is **injected**, not merely checked, so an agent cannot cause a full
table scan by omitting one.

---

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — system and data flow
- [`docs/agents.md`](docs/agents.md) — agent responsibilities and boundaries
- [`docs/security.md`](docs/security.md) — threat model and controls
- [`docs/roadmap.md`](docs/roadmap.md) — what to build next, in order

---

## Repository layout

```
insight-os/
├── apps/
│   ├── api/                    FastAPI backend
│   │   ├── app/
│   │   │   ├── agents/         orchestrator, critic
│   │   │   ├── alerts/         rule compiler, evaluation engine
│   │   │   ├── analytics/      rootcause, forecast, anomaly, profiling,
│   │   │   │                   significance, confidence, causal, simulation,
│   │   │   │                   panels
│   │   │   ├── api/v1/         routes
│   │   │   ├── core/           config, security, rate limiting
│   │   │   ├── data/           demo warehouse provider
│   │   │   ├── datasources/    crawler, secrets, registry, SQL provider
│   │   │   ├── db/             SQLAlchemy models, session, portable types
│   │   │   ├── evaluation/     golden cases + scored harness
│   │   │   ├── llm/            provider gateway + router
│   │   │   ├── ml/             AutoML with baseline and leakage gates
│   │   │   ├── notifications/  delivery channels + retry
│   │   │   ├── rag/            chunking, hybrid index, injection defence
│   │   │   ├── repositories/   identity, investigations, alerts, audit
│   │   │   ├── reports/        Markdown and PDF export
│   │   │   ├── semantic/       metric registry
│   │   │   ├── sql_engine/     validator, executor, text-to-SQL generator
│   │   │   └── workers/        scheduled alert sweep
│   │   ├── migrations/         Alembic
│   │   └── tests/              359 tests
│   └── web/                    Next.js 15 frontend
├── scripts/seed_data.py        synthetic warehouse generator
├── docs/
├── docker-compose.yml
└── .env.example
```

## Licence

MIT.
