# Roadmap

Ordered by dependency, not by spec section number. Each phase lands with tests
before the next begins.

## Done

### Phase 1 — Persistence and identity ✅
Auth backed by Postgres, Alembic migrations with a CI drift check, refresh-token
rotation with reuse detection, WebSocket ticket auth, rate limiting, audit-log
writes on auth/alert/investigation routes, and structural tenant isolation at
the session layer.

### Phase 6a — Alerts (batch half) ✅
Natural-language rules compiled to an inspectable structured form, backtested
over history before saving, and evaluated hourly by an arq worker. Kafka
streaming is still not built, and is still not the bottleneck.

### Phase 6b — Causal inference and simulation ✅
Difference-in-differences with cluster-robust inference, gated by parallel-trends
and placebo diagnostics. Scenario simulation with sensitivity analysis and
break-even. Both report their assumptions in the response body rather than in
documentation.

### Phase 7 — Evaluation harness ✅
Six golden cases with mechanically known ground truth, scoring driver recall,
restraint on cases with no finding, calibration, grounding catch rate and SQL
block rate. Runs in CI as a gate, not a report. It has already caught four real
bugs.

### Phase 2 — Real data sources ✅
`SQLExecutor` runs validated SQL against a live warehouse inside a read-only
transaction with a statement timeout and a row cap. `SchemaCrawler` discovers
tables and columns into `datasets` / `dataset_columns`, and the validator's
catalog is derived from that crawl rather than hand-written — so it cannot drift
stale-permissive. Credentials are resolved through a `SecretResolver` from a
reference; a pasted DSN is rejected at the API boundary.

`SQLDataProvider` generates SQL from the governed metric definition and is a
drop-in for `demo_data_provider`, verified by running the full orchestrator
against a real database.

Still outstanding within this area: `EXPLAIN`-based cost checks before
execution, incremental re-crawl instead of full replacement, and connectors
beyond Postgres and SQLite.

### Phase 3 — Text-to-SQL ✅
`SQLGenerator` builds schema context from the crawled catalog, generates SQL
through the LLM gateway, and gates it on the same validator that guards
hand-written queries. Rejections are fed back as repair prompts carrying the
specific rule broken and the validator's nearest-match hint; attempts are capped
at three and all of them are returned. With no model configured it declines
rather than guessing. Exposed as `POST /api/v1/query/explain` (generate and
validate, do not run) and `/ask`.

### Phase 8 — Notification delivery ✅
Channels for webhook, email and log, with bounded retries that distinguish
transient failures from permanent ones, per-channel isolation so one broken
destination cannot suppress another, and a receipt for every attempt. The alert
sweep returns delivery receipts in its job result.

## Next

### Phase 4 — AutoML ✅
Task detection, three candidates per task, temporal splits where a date column
exists, a trivial baseline every model has to beat by more than the standard
error of the measurement, calibration-aware selection, permutation importance on
held-out data, and target-leakage detection. Runs are written to a local JSON
experiment log; `RunRecorder` is the MLflow swap point. SHAP is listed as an
optional extra — permutation importance is model-agnostic and needs no
dependency.

### Phase 9 — Report export ✅
Markdown and PDF, with the rule that no figure is exported without its
qualifiers: confidence carries its limiting factor, drivers carry both
contribution and own change, causal estimates carry their diagnostics, and
expected impact carries the assumption behind it.

### Phase 5 — RAG ✅
Structure-aware chunking that carries the heading trail into the index, hybrid
retrieval fusing BM25 and character n-grams by reciprocal rank, per-workspace
indexes, and mandatory citations. The prompt-injection critic check shipped in
the same phase, as promised: retrieved text is delimited as data, scanned at
both ingestion and retrieval, and the critic blocks any answer that adopts an
instruction found in a document.

Outstanding within this area: pgvector persistence (the index is in-process),
a dense embedding signal (the slot exists and is unfilled), and reranking.

### Phase 8 — Notification delivery (~1 week)
The sweep produces payloads and has nowhere to send them. Email, Slack and
webhooks, with per-channel retry and a delivery log.

## Continuous
Remaining frontend routes (metrics catalogue, data sources, ad-hoc query,
simulation UI),
OpenTelemetry wiring, Grafana dashboards, Playwright E2E tests, and moving the
metric registry to a read-through cache over the `metrics` table.

## Deliberately deferred

**Kafka streaming (spec §34)** and **knowledge graph (spec §22)**. Streaming
before the batch alert engine has real data sources behind it would be
architecture with nothing underneath, and the knowledge graph duplicates what
the semantic layer already governs. Both are worth building later; neither is
worth building next.
