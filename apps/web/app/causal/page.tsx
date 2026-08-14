"use client";

import { useState } from "react";
import { ErrorNote, PageHeading, Shell, SignInRequired } from "@/components/Shell";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { CausalResponse } from "@/lib/types";

const compact = (value: number) =>
  new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);

const DEFAULTS = {
  metric_key: "revenue",
  dimension: "region",
  treated: "South",
  treatment_date: "2025-06-16",
  start: "2025-02-01",
  end: "2025-10-01",
  grain: "week",
};

const field =
  "w-full rounded-lg border border-hairline bg-elevated px-3 py-2 font-mono text-xs text-ink focus:border-cyan-dim focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan/40";
const label = "block font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted";

export default function CausalPage() {
  const { status } = useSession();
  const [form, setForm] = useState(DEFAULTS);
  const [result, setResult] = useState<CausalResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const update = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((prev) => ({ ...prev, [key]: e.target.value }));

  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(
        await api.diffInDiff({
          metric_key: form.metric_key,
          dimension: form.dimension,
          treated_units: form.treated.split(",").map((s) => s.trim()).filter(Boolean),
          treatment_date: form.treatment_date,
          start: form.start,
          end: form.end,
          grain: form.grain,
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The estimate could not be computed.");
    } finally {
      setBusy(false);
    }
  };

  if (status !== "signed-in") {
    return (
      <Shell>
        <SignInRequired what="run a causal estimate" />
      </Shell>
    );
  }

  const estimate = result?.estimate;

  return (
    <Shell>
      <PageHeading eyebrow="Causal inference" title="Did the intervention cause it?">
        Difference-in-differences compares the treated group against a control group over the
        same window. Two diagnostics decide whether the number is allowed to be called causal
        at all — if either fails, the estimate is withheld rather than qualified in a footnote.
      </PageHeading>

      <form
        onSubmit={run}
        className="grid gap-4 rounded-2xl border border-hairline bg-surface p-6 md:grid-cols-3"
      >
        <div>
          <label className={label} htmlFor="metric">Metric</label>
          <input id="metric" className={`${field} mt-1.5`} value={form.metric_key} onChange={update("metric_key")} />
        </div>
        <div>
          <label className={label} htmlFor="dimension">Split by</label>
          <input id="dimension" className={`${field} mt-1.5`} value={form.dimension} onChange={update("dimension")} />
        </div>
        <div>
          <label className={label} htmlFor="treated">Treated units</label>
          <input id="treated" className={`${field} mt-1.5`} value={form.treated} onChange={update("treated")} />
        </div>
        <div>
          <label className={label} htmlFor="treatment-date">Intervention date</label>
          <input id="treatment-date" type="date" className={`${field} mt-1.5`} value={form.treatment_date} onChange={update("treatment_date")} />
        </div>
        <div>
          <label className={label} htmlFor="start">From</label>
          <input id="start" type="date" className={`${field} mt-1.5`} value={form.start} onChange={update("start")} />
        </div>
        <div>
          <label className={label} htmlFor="end">To</label>
          <input id="end" type="date" className={`${field} mt-1.5`} value={form.end} onChange={update("end")} />
        </div>
        <div className="md:col-span-3">
          <button
            type="submit"
            disabled={busy}
            className="rounded-lg bg-cyan px-4 py-2 font-mono text-xs font-medium text-base transition-opacity hover:opacity-90 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan"
          >
            {busy ? "Estimating…" : "Estimate the effect"}
          </button>
        </div>
      </form>

      {error ? (
        <div className="mt-6">
          <ErrorNote message={error} fix="Widen the date range or pick a different treated unit." />
        </div>
      ) : null}

      {estimate ? (
        <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_1.1fr]">
          {/* The estimate. Struck through and desaturated when the design fails,
              so the number is visibly withheld rather than quietly footnoted. */}
          <section
            className={`rounded-2xl border p-6 ${
              estimate.credible ? "border-cyan-dim/50 bg-surface" : "border-crit/40 bg-crit/5"
            }`}
          >
            <p className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted">
              Average effect on the treated
            </p>
            <p
              className={`mt-3 font-display text-5xl font-black tracking-tight tabular-nums ${
                estimate.credible ? "text-ink" : "text-ink-faint line-through decoration-crit/70 decoration-2"
              }`}
            >
              {estimate.att > 0 ? "+" : "−"}
              {compact(Math.abs(estimate.att))}
            </p>
            {estimate.relative_att !== null ? (
              <p className="mt-1 font-mono text-sm text-ink-muted tabular-nums">
                {(estimate.relative_att * 100).toFixed(1)}% per unit-period
              </p>
            ) : null}

            {estimate.credible ? (
              <dl className="mt-6 space-y-2 font-mono text-xs">
                <div className="flex justify-between">
                  <dt className="text-ink-faint">95% interval</dt>
                  <dd className="tabular-nums text-ink">
                    {compact(estimate.ci_95[0])} … {compact(estimate.ci_95[1])}
                  </dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-faint">p-value</dt>
                  <dd className="tabular-nums text-ink">{estimate.p_value.toFixed(4)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-faint">clusters</dt>
                  <dd className="tabular-nums text-ink">{estimate.sample.clusters}</dd>
                </div>
              </dl>
            ) : (
              <p className="mt-6 text-sm leading-relaxed text-crit">
                Withheld. The difference in the data is real, but this design cannot attribute
                it to the intervention.
              </p>
            )}

            <p className="mt-6 border-t border-hairline pt-4 text-sm leading-relaxed text-ink-muted">
              {estimate.interpretation}
            </p>
          </section>

          <section className="space-y-4">
            <div className="rounded-2xl border border-hairline bg-surface p-6">
              <h2 className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted">
                Diagnostics
              </h2>
              <ul className="mt-4 space-y-4">
                {estimate.diagnostics.map((diagnostic) => (
                  <li key={diagnostic.name} className="flex gap-3">
                    <span
                      aria-hidden
                      className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${
                        diagnostic.passed ? "bg-ok" : "bg-crit"
                      }`}
                    />
                    <div>
                      <p className="font-mono text-xs text-ink">
                        {diagnostic.name.replace(/_/g, " ")}{" "}
                        <span className={diagnostic.passed ? "text-ok" : "text-crit"}>
                          {diagnostic.passed ? "passed" : "failed"}
                        </span>
                        <span className="text-ink-faint"> · p={diagnostic.p_value.toFixed(4)}</span>
                      </p>
                      <p className="mt-1 text-xs leading-relaxed text-ink-muted">
                        {diagnostic.detail}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>

            <div className="rounded-2xl border border-hairline bg-surface p-6">
              <h2 className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted">
                What this assumes
              </h2>
              <ul className="mt-3 space-y-2 text-xs leading-relaxed text-ink-muted">
                {estimate.caveats.map((caveat) => (
                  <li key={caveat} className="flex gap-2">
                    <span aria-hidden className="text-ink-faint">
                      —
                    </span>
                    {caveat}
                  </li>
                ))}
              </ul>
              {result?.panel.note ? (
                <p className="mt-4 border-t border-hairline pt-3 font-mono text-[11px] leading-relaxed text-ink-faint">
                  {result.panel.note}
                </p>
              ) : null}
            </div>
          </section>
        </div>
      ) : null}
    </Shell>
  );
}
