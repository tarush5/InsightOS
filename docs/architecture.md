# Architecture

## Request path

```
Browser
  │  WebSocket /api/v1/investigations/stream
  ▼
FastAPI  ──►  InvestigationOrchestrator
                 │
                 ├─► MetricRegistry        resolve a governed metric definition
                 ├─► SQLValidator          parse, authorize, rewrite, cap
                 ├─► data_provider         execute against the workspace source
                 ├─► DataProfiler          quality score + issues
                 ├─► AnomalyDetector       robust z / seasonal residual / rolling
                 ├─► RootCauseEngine       exact contribution decomposition
                 ├─► significance          Welch t / two-proportion z
                 ├─► Forecaster            damped Holt-Winters + backtest
                 ├─► LLMGateway            plan + narrate (optional)
                 └─► CriticAgent           verify narrative against evidence
                          │
                          ▼
                   confidence.combine()    weighted geometric mean
```

## The trust boundary

Everything above `LLMGateway` is deterministic. The gateway is the only place a
model can influence output, and everything it produces passes through
`CriticAgent` before reaching a user.

This ordering matters. The common failure mode for LLM analytics is a fluent
narrative containing a number nobody computed. Here the narrative is the
*untrusted* artifact: `CriticAgent._check_numbers` extracts every numeric token
from the prose and requires a match, within 1.5% tolerance, against a value in
the computed evidence tree. Unmatched numbers fail a blocking check and the
conclusion is replaced with "insufficient evidence to make a reliable
conclusion."

## Why the geometric mean

`confidence.combine` multiplies weighted component scores rather than averaging
them. With an arithmetic mean, three healthy components hide one broken one —
95% data, 95% model, 95% reasoning and 5% statistical averages to 72%, which
reads as a usable answer. The geometric mean returns 32% and names
`statistical` as the limiting factor, which is the truthful summary.

## Tenant isolation

Isolation is structural, not procedural:

1. `WorkspaceScoped` asserts at class-declaration time that every tenant-owned
   model defines `workspace_id`.
2. JWTs carry the workspace id; `Principal` cannot be constructed without one.
3. `Catalog` is built per workspace, so the SQL validator's table allowlist is
   already scoped before a statement is parsed — a cross-tenant table name fails
   as `unknown_table`.
4. `session_scope(workspace_id)` attaches a `with_loader_criteria` for every
   `WorkspaceScoped` model, so SQLAlchemy adds the tenant predicate to every
   SELECT — including relationship loads and queries written with no filter.
   Route handlers never write the clause themselves, and omitting it returns
   nothing rather than another tenant's rows.

## Two analyses the orchestrator does not run

Root-cause decomposition answers *what moved*. Two further questions need their
own entry points, because both are easy to answer wrongly by default:

**`DiffInDiff`** answers *did the intervention cause it*. Two-way fixed effects
with cluster-robust standard errors and t(G−1) inference, gated by a
parallel-trends pre-test and a placebo test. An estimate failing either
diagnostic is returned with `credible=False` and the number withheld — the UI
strikes it through rather than footnoting it. Measured CI coverage is 92.5%
against a nominal 95% with 8 clusters, which is the known few-cluster problem;
the caveat says the interval is optimistic instead of claiming otherwise.

**`ScenarioSimulator`** answers *what if*. It propagates stated assumptions
through a segment decomposition — arithmetic, not causality — and says so. Every
lever carries a `basis` (`asserted` / `historical` / `modelled`), and asserted
levers are labelled in the output so a guess is never laundered into an
estimate. The sensitivity tornado exists so the argument moves to the assumption
the answer actually rests on.

## Panels and calendar edges

`build_panel` drops incomplete boundary periods when resampling daily rows to
weeks or months, and reports what it dropped. A week holding two days reads as a
70% collapse, and it lands at exactly the point where a fitted trend is most
sensitive. Keeping those buckets flipped South's parallel-trends test from
p=0.32 to p=0.097 on the demo warehouse — turning a genuine, credible effect into
"not usable as a causal estimate". Interior gaps are deliberately kept: a hole in
the middle is a data problem the caller needs to see.

## Deliberate deferrals

**Vector store.** `pgvector` is in the compose file and the `documents` /
`embeddings` tables are designed, but retrieval is not built. A half-built RAG
path that silently returns nothing is worse than an absent one.

**MLflow.** `MLModel` carries `mlflow_run_id` and a metrics blob. The AutoML
trainer is not written. The forecaster already does the thing that matters —
rolling-origin backtesting with a baseline comparison — so model claims are
measured rather than asserted.

**Kafka.** Alerts compile natural language to a structured rule and are
evaluated hourly by an arq worker. Streaming is a genuine architectural
addition, not a config change, and is worth less than giving the batch path real
data sources first.

## The evaluation harness is a gate, not a report

`app/evaluation/` runs six golden cases whose ground truth is known by
construction — each generates its own data from a seeded process, so the labels
cannot drift from the data they describe and cannot have come from the system
under test.

Two of the six contain nothing to find. That is the point: a system scored only
on cases with a real answer learns to always produce one, so **restraint** is
scored separately from recall. Calibration is reported as the gap between
confidence on correct and incorrect cases rather than as an accuracy, because a
well-calibrated 40% is a good answer and rewarding higher confidence would train
the wrong behaviour.

It runs in CI with thresholds that fail the build. It has already caught four
real defects: degenerate dimensions outranking the true driver, unqualified
columns bypassing the SQL validator, partial comparison windows manufacturing
changes, and calendar-edge periods poisoning trend estimation.


## Machine learning is gated, not celebrated

`app/ml/automl.py` is the one module where the interesting code is entirely in
the checks. Fitting three sklearn pipelines is nine lines; the rest decides
whether the resulting score should be believed.

The ordering matters. A run reports its baseline before its model, because
predicting the majority class scores 89% accuracy on an 11% churn rate and any
number shown without that context reads as a success. It reports Brier alongside
AUC, because discrimination and calibration are different properties and the
best model on one is regularly not the best on the other — on the demo data the
AUC winner emits probabilities worse than the base rate. And it treats a
suspiciously perfect feature as a defect to investigate rather than a result to
report.

Two of those checks were wrong in their first version and were caught by tests
written against known-null data: a flat 0.02 AUC margin that passed noise, and a
selection rule that ignored calibration entirely. Both are the same failure —
measuring the model without measuring the uncertainty in the measurement.

## Export carries the qualifiers or it is not an export

`app/reports/export.py` renders a stored investigation to Markdown or PDF, and
the format rule is that no figure appears without what qualifies it. This is a
design constraint rather than a formatting preference: the export is the artefact
that reaches the meeting, and the parts most likely to be trimmed for brevity —
the limiting factor, the diagnostics, the assumption behind an impact estimate —
are precisely the parts that tell a reader how much weight the number can bear.
