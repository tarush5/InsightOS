# Agents

The spec warns against creating agents to inflate an agent count. This
implementation has **two** true agents and a set of deterministic engines.

## InvestigationOrchestrator

Owns the 14-stage pipeline and emits a typed event per stage transition. It
holds no model access of its own beyond planning and narration, and it never
performs arithmetic — every figure comes from an engine in `app/analytics/`.

Stages: understand → plan → resolve_metric → build_query → validate_sql →
execute → profile → anomaly → root_cause → significance → forecast →
recommend → verify → complete.

## CriticAgent

Verification only. It holds no tools and cannot query data, which keeps it cheap
and hard to subvert — there is no data path for a prompt injection to reach.

Blocking checks (failure suppresses the conclusion):

| Check | Fails when |
|---|---|
| `sql_validated` | a statement reached execution unvalidated |
| `result_non_empty` | the query returned no rows |
| `attribution_reconciles` | contributions don't sum to the headline change |
| `numbers_grounded` | the narrative contains an uncomputed number |
| `causal_claims_supported` | causal phrasing without a significant test result |

Advisory checks: `forecast_beats_baseline`, `evidence_attached`.

## Why the others are engines, not agents

`RootCauseEngine`, `Forecaster`, `AnomalyDetector`, `DataProfiler`, `DiffInDiff`
and `ScenarioSimulator` are pure functions of their inputs. Wrapping them in an
agent loop would add latency, cost and non-determinism to operations that have
exactly one correct answer. Reserve agents for genuine tool-use and branching
judgement.

The "Simulation Agent" in the spec is a good example. Scenario simulation is
arithmetic over stated assumptions — there is no branching judgement for a model
to exercise, and letting one choose the numbers would be strictly worse than
making the caller state them. It is `app/analytics/simulation.py`, and the
honesty work went into labelling each lever's `basis` instead.

Causal inference is the same argument with higher stakes. `DiffInDiff` decides
whether an estimate may be called causal using two statistical tests, not a
judgement call. That decision is the single most consequential one the system
makes, and it should not be delegated to something that can be talked out of it.

## The alert rule compiler is deliberately not an agent

`compile_rule` is a deterministic parser. An alert that fires at 3am has to be
explainable without reference to a model's mood, so the rule is compiled once,
read back to the author in structured form, and stored.

When the text is ambiguous it raises `AmbiguousRule` naming exactly what was
missing — threshold, direction or metric — rather than guessing. A wrong guess
here produces either silent misses or pager fatigue, both worse than a question.
An LLM may sit upstream to rephrase a request into this grammar, but it never
emits a rule directly: `compile_rule` is the only path to an `AlertRule`.

## The SQL generator is the one place a model writes something executable

`SQLGenerator` is not an agent either — it has no tools and no branching — but
it is the only component whose output becomes a database instruction, so it gets
the strictest treatment in the codebase.

The generate/validate/repair loop is worth reading as a pattern. The validator
is not a filter bolted on afterwards; it is the thing that makes generation
viable, and its error messages were already written as instructions to a person,
which turns out to be exactly what a repair prompt needs. Two attempts is
usually enough. A third rarely helps, which is why the cap is three rather than
"until it works".

The refusal path matters as much as the success path. The repair prompt tells
the model it may answer `UNANSWERABLE`, and the loop honours that immediately
instead of spending the remaining retries pushing it to invent a table it has
already said does not exist.

## The critic gained the check that retrieval required

Adding RAG changed the critic's job. Until documents existed, every input came
from the workspace's own warehouse; a retrieved passage is text somebody else
wrote, and it can contain instructions.

`no_injected_instructions` is a blocking check, and its design is worth noting:
it does not test whether the *passage* looks like an attack — the scanner does
that, and a scanner can be written around. It tests whether the *answer* adopted
directive content. That framing is why it holds against phrasings nobody has
seen, and it is the same reasoning as `numbers_grounded`: check the output
against what it should be, not the input against what it should not.

## Not yet built

Data Agent (schema exploration), EDA Agent, ML Agent. Each should be added only
when it needs to *choose* between tools — otherwise it belongs in
`app/analytics/`.
