"use client";

/**
 * Every conclusion carries the artifacts it was built from (spec section 30).
 * SQL is rendered as text, never executed client-side, and the metric definition
 * is shown verbatim so a reader can dispute the definition rather than the number.
 */
export function EvidenceDrawer({ evidence }: { evidence: Record<string, unknown> }) {
  const sql = typeof evidence.sql === "string" ? evidence.sql : null;
  const metric = evidence.metric as Record<string, unknown> | undefined;
  const significance = evidence.significance as Record<string, unknown> | null | undefined;
  const profile = evidence.profile as Record<string, unknown> | undefined;

  return (
    <section className="panel p-6" aria-label="Evidence">
      <h2 className="label-mono mb-4">Why this insight?</h2>

      <div className="space-y-3">
        {metric && (
          <details className="rounded-lg border border-hairline bg-base/50 p-3">
            <summary className="cursor-pointer text-sm">
              <span className="text-ok" aria-hidden>✓</span>{" "}
              Metric definition — <span className="font-mono text-cyan">{String(metric.key)}</span>
            </summary>
            <dl className="mt-3 space-y-2 text-xs">
              <div><dt className="label-mono">Expression</dt>
                <dd className="mt-1 font-mono text-ink-muted">{String(metric.expression)}</dd></div>
              <div><dt className="label-mono">Definition</dt>
                <dd className="mt-1 text-ink-muted">{String(metric.description)}</dd></div>
              <div><dt className="label-mono">Governance</dt>
                <dd className="mt-1 text-ink-muted">
                  v{String(metric.version)} · {String(metric.status)} · owner {String(metric.owner)}
                </dd></div>
            </dl>
          </details>
        )}

        {sql && (
          <details className="rounded-lg border border-hairline bg-base/50 p-3">
            <summary className="cursor-pointer text-sm">
              <span className="text-ok" aria-hidden>✓</span> Executed SQL (validated, read-only)
            </summary>
            <pre className="mt-3 overflow-x-auto rounded-md bg-base p-3 font-mono text-xs leading-relaxed text-ink-muted">
              {sql}
            </pre>
          </details>
        )}

        {significance && (
          <details className="rounded-lg border border-hairline bg-base/50 p-3">
            <summary className="cursor-pointer text-sm">
              <span className="text-ok" aria-hidden>✓</span> Statistical test —{" "}
              <span className="font-mono">{String(significance.test)}</span>
            </summary>
            <p className="mt-3 text-xs text-ink-muted">{String(significance.interpretation)}</p>
            <p className="mt-2 font-mono text-xs text-ink-faint">
              p = {String(significance.p_value)} · d = {String(significance.effect_size)} ·
              n = {String(significance.n_prev)}/{String(significance.n_curr)}
            </p>
          </details>
        )}

        {profile && (
          <details className="rounded-lg border border-hairline bg-base/50 p-3">
            <summary className="cursor-pointer text-sm">
              <span className="text-ok" aria-hidden>✓</span> Source data quality
            </summary>
            <p className="mt-3 font-mono text-xs text-ink-muted">
              {String(profile.quality_score)}/100 across {String(profile.row_count)} rows
            </p>
          </details>
        )}
      </div>
    </section>
  );
}
