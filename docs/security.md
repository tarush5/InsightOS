# Security

## Threat model

| Threat | Control | Verified |
|---|---|---|
| SQL injection via generated SQL | AST allowlist; single statement only | `test_sql_security.py` |
| Destructive statements | root node must be SELECT/UNION/CTE | ✓ 8 statement types |
| Credential exfiltration | `pg_catalog`/`information_schema` blocked | ✓ |
| Filesystem/network egress via SQL | function denylist (`pg_read_file`, `dblink`, …) | ✓ |
| Model hallucinating tables | catalog check, fails closed with nearest-match hint | ✓ |
| Model hallucinating columns | qualified **and** unqualified columns resolved against the catalog | ✓ |
| Unbounded scans | `LIMIT` injected, not merely validated | ✓ |
| Cross-tenant reads | session-level `with_loader_criteria` on every workspace-owned table | `test_persistence.py` |
| Cross-tenant reads by guessed id | lookup by reference returns nothing outside the workspace | ✓ |
| Privilege escalation | RBAC matrix, per-permission route guards | ✓ |
| Account enumeration | login returns one error and one timing profile for both failure modes | ✓ |
| Password guessing | PBKDF2 + 10 attempts / 5 min per identity | `test_api_integration.py` |
| Refresh token theft | rotation on every use; reuse revokes every session for that user | ✓ |
| Credential leakage into logs | WebSocket authenticated by a 60s ticket, never the access token | ✓ |
| Fabricated conclusions | critic blocking checks; every figure matched to computed evidence | `test_critic.py` |
| Causal overclaiming | parallel-trends and placebo diagnostics gate the estimate | `test_causal.py` |
| Secrets in the browser | provider keys server-side only; `NEXT_PUBLIC_` prefix required for exposure | by construction |
| Stack traces leaking internals | global handler returns a structured error, never the traceback | ✓ |
| Writes reaching the warehouse | read-only transaction *and* AST allowlist, independently | `test_datasources.py` |
| Runaway queries | statement timeout + row cap, enforced on fetch not just by LIMIT | ✓ |
| Credentials at rest | `secret_ref` is a pointer; a pasted DSN is refused at the API | ✓ |
| Credentials in logs and tracebacks | DSNs redacted; `SQLExecutor.__repr__` never renders the password | ✓ |
| Catalog drift going stale-permissive | catalog derived from the crawl; dropped tables disappear | ✓ |
| Self-alias bypassing column checks | `SELECT x AS x` no longer exempts `x` from the catalog check | ✓ |
| Model-generated SQL | validated identically to hand-written; never executed on plausibility | `test_text_to_sql.py` |
| Model widening its own access | schema context built from the crawled catalog, never from the question | ✓ |
| Credential echoed in a 422 body | secret refs validated in the handler, so the value is never repeated back | `test_api_integration.py` |
| Alert body on the wire in clear text | webhook URLs must be https | ✓ |
| Training on a table outside the catalog | table and target checked against the crawl before any query | `test_api_integration.py` |
| Model trained on leaked target | single-feature AUC ≥ 0.98 flagged; model marked unusable | `test_automl.py` |
| Exported reports stripped of caveats | qualifiers rendered with every figure, enforced by test | `test_reports.py` |
| Prompt injection via ingested documents | detection, data-boundary delimiting, and a blocking critic check | `test_rag.py` |
| Poisoned document ingested unnoticed | scanned at ingestion; the uploader is warned | ✓ |
| Cross-tenant document retrieval | one index per workspace, not one index with a filter | ✓ |

## Tenant isolation

Isolation is structural rather than conventional. `session_scope(workspace_id)`
registers a `with_loader_criteria` for every model inheriting `WorkspaceScoped`,
so SQLAlchemy appends the tenant predicate to every SELECT that touches those
tables — including relationship loads and queries written with no filter at all.

Route handlers therefore do **not** write `WHERE workspace_id = ...`. Forgetting
that clause is the most common multi-tenant leak, and the design makes it
impossible for the omission to leak: an unfiltered query returns nothing rather
than someone else's rows. `test_a_scoped_session_cannot_see_another_workspace`
deliberately issues an unfiltered `select(Investigation)` to prove it.

## Password storage

PBKDF2-HMAC-SHA256, 390,000 rounds (the OWASP 2023 floor), 16-byte random salt,
constant-time compare. Minimum length 12.

The round count is configurable, because full-strength hashing on every test
fixture makes the suite slow enough that people stop running it. It can only be
lowered in local development: `Settings` refuses a value below the floor when
`ENV` is staging or production. Each stored hash records the count it was
written with, so lowering the setting never weakens an existing hash.

Argon2id would be preferable; PBKDF2 avoids a native dependency in the default
install.

## Three independent limits on a query

The validator decides whether a statement is *allowed*. The executor decides
what it is *able* to do, and the two are deliberately independent, because a
parser that gets a dialect quirk wrong should not be the only thing standing
between a generated statement and the warehouse.

| Limit | Stops | Fails without it |
|---|---|---|
| Read-only transaction | writes the parser missed | data modified by generated SQL |
| Statement timeout | a query that planned badly | a connection held until someone notices |
| Row cap on fetch | a correct, fast, enormous result | API memory exhausted |

The row cap is enforced on fetch as well as by the injected `LIMIT`, because a
`LIMIT` inside a CTE does not bound the outer result.

A backend that enforces less says so. SQLite has no statement timeout, so every
result from it carries a warning naming that gap rather than being presented as
equivalently protected.

## PII classification is a starting point, not a guarantee

The crawler flags columns whose name or sampled values look like personal data,
and labels each flag with its basis (`name` or `sampled_values`). It is a
heuristic and the crawl result says so in its warnings.

That framing is load-bearing. A classifier presented as authoritative is worse
than none, because it stops people looking. The first version was also too
eager — a loose phone pattern matched `order_date` and `total_amount`, since
both are digits and separators — and a classifier that cries wolf on every
numeric column gets switched off. Value patterns now run only on text columns,
using the type information the crawler already has.

## Prompt injection: three layers, and why the third is the one that matters

Retrieval is the first feature in this system that puts text somebody else wrote
into a model's context. Every prior input came from the workspace's own warehouse
or from a user typing.

**Layer 1 — detection.** `app/rag/injection.py` scores instruction-shaped content
at both ingestion and retrieval. Patterns target the model-addressing *shape*
("ignore all previous instructions", "you are now"), not imperative mood in
general — real policy documents are full of imperatives, and flagging them would
make the signal useless. Content above the block threshold never reaches the
model; content above the flag threshold is annotated in place.

**Layer 2 — delimiting.** Passages are wrapped in an explicit data boundary with
deliberately unusual markers, since an attacker will try closing a conventional
`</context>`. The wrapper states that the content is data to cite and that
directions inside it are to be reported, not followed.

**Layer 3 — the critic.** `CriticAgent` gains a blocking `no_injected_instructions`
check when passages were retrieved. Layers 1 and 2 depend on recognising an
attack; an attacker who knows the patterns phrases around them. This layer looks
at the outcome instead — whether the answer began following directions that
appeared in retrieved content — so it does not need to have seen the phrasing
before.

Suspicious lines are annotated rather than deleted, deliberately. Silently
editing a source makes any quotation of it wrong and hides the attack from the
person reviewing the answer.

## Why generated SQL is safe to run at all

Nothing about the generator is trusted. It has no database handle, produces a
string, and that string goes through the identical path as one a user typed:

```
question -> model -> SQL string -> validator (AST allowlist, catalog, LIMIT)
         -> executor (read-only txn, statement timeout, row cap) -> rows
```

Two boundaries carry the weight. The schema context comes from the crawled
catalog rather than from the question, so a model cannot widen its own access by
asking about a table it was not told exists — the catalog check rejects the name
regardless of what the prompt said. And generation and execution validate
independently; if they ever disagree, `/ask` returns a 500 naming that
disagreement as a defect rather than retrying, because a query that passes one
gate and fails the other means one of them is wrong.

## Writes that must survive their own exception

Security-relevant writes are committed *before* the exception that reports them
is raised. The session rolls back on exception, so recording a failed login or
revoking sessions after refresh-token reuse and then raising a 401 would discard
exactly the write that mattered — while the caller still saw the 401 and
reasonably assumed the sessions had been cut. That was a real bug, found by
`test_replaying_a_rotated_token_revokes_every_session`.

## PII and model training

`app/ml` does not consult the crawler's PII flags, and it should before this is
used on production data — a model trained on `email` or `postcode` will happily
learn a proxy for the individual. The crawler already records `contains_pii` per
column and `AutoML.train` accepts an `exclude` list, so wiring the default is
small; it is listed below rather than claimed.

## Known gaps

These are not safe to ship. They are listed rather than hidden.

- **Browser token storage.** The web client holds tokens in memory only, so a
  reload signs the user out. This is deliberate — `localStorage` is readable by
  any injected script — but the real fix is httpOnly cookies issued by the API,
  which it does not yet do.
- **Rate limiting degrades to per-process** when Redis is unavailable. The
  limiter reports its backend rather than silently claiming to be distributed,
  but a multi-replica deployment without Redis is under-protected.
- **PII detection** is a column flag on `DatasetColumn` with no classifier
  behind it.
- **Audit coverage is partial.** Auth, alerts and investigations write entries;
  metric approval and data-source routes do not yet.
- **No notification delivery.** The alert sweep produces notification payloads
  and hands them to a callable; nothing sends email, Slack or webhooks.
- **Secrets are referenced, not managed.** `DataSource.secret_ref` points at a
  secret manager that is not wired up. `EnvSecretResolver` reads the process
  environment, which is adequate for a single deployment and not for many.
- **PII columns are not excluded from training by default.** See above.
- **The retrieval index is in process memory.** It does not survive a restart
  and does not shard. pgvector is in the stack and `RetrievalIndex.add`/`search`
  are the two methods a persistent backend must implement.
- **No dense retrieval signal.** The embedder slot is unfilled, so questions
  phrased with no shared vocabulary will not match. The index reports this
  rather than degrading quietly.
- **Training runs inline on the request thread.** A large table blocks a worker
  for the duration. It belongs in the arq queue with a job-status endpoint.
- **Model artefacts are not persisted.** A run reports its scores and is
  discarded; there is no registry, no versioning and no inference endpoint, so
  nothing can be served from a trained model yet.
