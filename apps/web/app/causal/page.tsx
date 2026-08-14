"use client";

import { useState, useEffect } from "react";
import { ErrorNote, PageHeading, Shell, SignInRequired } from "@/components/Shell";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { CausalResponse, MetricSummary } from "@/lib/types";

const compact = (value: number) =>
  new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);

const DEFAULTS = {
  metric_key: "revenue",
  dimension: "region",
  treated_units: "South",
  control_units: "",
  treatment_date: "2024-06-16",
  start: "2024-02-01",
  end: "2024-10-01",
  grain: "week",
};

const field =
  "w-full rounded-lg border border-hairline bg-elevated px-3 py-2 font-mono text-xs text-ink focus:border-cyan-dim focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan/40";
const label = "block font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted";

export default function CausalPage() {
  const { status } = useSession();
  const [metrics, setMetrics] = useState<MetricSummary[]>([]);
  const [form, setForm] = useState(DEFAULTS);
  const [result, setResult] = useState<CausalResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (status === "signed-in") {
      api.metrics().then((res) => setMetrics(res.metrics)).catch(() => {});
    }
  }, [status]);

  const update = (key: keyof typeof form) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>
  ) => setForm((prev) => ({ ...prev, [key]: e.target.value }));

  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const treated = form.treated_units.split(",").map((s) => s.trim()).filter(Boolean);
      const control = form.control_units ? form.control_units.split(",").map((s) => s.trim()).filter(Boolean) : undefined;
      
      setResult(
        await api.diffInDiff({
          metric_key: form.metric_key,
          dimension: form.dimension,
          treated_units: treated,
          control_units: control && control.length > 0 ? control : undefined,
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
        same window.
      </PageHeading>

      <form
        onSubmit={run}
        className="grid gap-4 rounded-2xl border border-hairline bg-surface p-6 md:grid-cols-3"
      >
        <div>
          <label className={label} htmlFor="metric">Metric</label>
          <select id="metric" className={`${field} mt-1.5`} value={form.metric_key} onChange={update("metric_key")}>
            {metrics.length === 0 && <option value={form.metric_key}>{form.metric_key}</option>}
            {metrics.map(m => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
        </div>
        <div>
          <label className={label} htmlFor="dimension">Split by</label>
          <input id="dimension" className={`${field} mt-1.5`} value={form.dimension} onChange={update("dimension")} />
        </div>
        <div>
          <label className={label} htmlFor="grain">Grain</label>
          <select id="grain" className={`${field} mt-1.5`} value={form.grain} onChange={update("grain")}>
            <option value="day">Day</option>
            <option value="week">Week</option>
            <option value="month">Month</option>
          </select>
        </div>
        <div>
          <label className={label} htmlFor="treated">Treated units (comma-separated)</label>
          <input id="treated" className={`${field} mt-1.5`} value={form.treated_units} onChange={update("treated_units")} />
        </div>
        <div>
          <label className={label} htmlFor="control">Control units (optional)</label>
          <input id="control" className={`${field} mt-1.5`} value={form.control_units} onChange={update("control_units")} />
        </div>
        <div>
          <label className={label} htmlFor="treatment-date">Intervention date</label>
          <input id="treatment-date" type="date" className={`${field} mt-1.5`} value={form.treatment_date} onChange={update("treatment_date")} />
        </div>
        <div>
          <label className={label} htmlFor="start">From Date</label>
          <input id="start" type="date" className={`${field} mt-1.5`} value={form.start} onChange={update("start")} />
        </div>
        <div>
          <label className={label} htmlFor="end">To Date</label>
          <input id="end" type="date" className={`${field} mt-1.5`} value={form.end} onChange={update("end")} />
        </div>
        <div className="md:col-span-3">
          <button
            type="submit"
            disabled={busy}
            className="rounded-lg bg-cyan px-4 py-2 font-mono text-xs font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-50"
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
        <div className="mt-8 grid gap-6 lg:grid-cols-[1.2fr_1fr] animate-riseIn">
          <section
            className={`rounded-2xl border p-6 ${
              estimate.credible ? "border-cyan-dim/50 bg-surface" : "border-crit/40 bg-crit/5"
            }`}
          >
            <div className="flex justify-between items-start mb-6">
              <div>
                <p className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted">
                  Average Treatment Effect on Treated (ATT)
                </p>
                <p
                  className={`mt-2 font-display text-5xl font-black tracking-tight tabular-nums ${
                    estimate.credible ? "text-ink" : "text-ink-faint line-through decoration-crit/70 decoration-2"
                  }`}
                >
                  {estimate.att > 0 ? "+" : "−"}
                  {compact(Math.abs(estimate.att))}
                </p>
                {estimate.relative_att !== null && estimate.relative_att !== undefined ? (
                  <p className="mt-1 font-mono text-sm text-ink-muted tabular-nums">
                    {(estimate.relative_att * 100).toFixed(1)}% relative
                  </p>
                ) : null}
              </div>
              <div className={`px-3 py-1 rounded-full font-mono text-xs ${estimate.p_value < 0.05 ? "bg-ok/20 text-ok" : "bg-ink-faint/20 text-ink-muted"}`}>
                {estimate.p_value < 0.05 ? "Significant" : "Not Significant"} (p={estimate.p_value.toFixed(4)})
              </div>
            </div>

            {estimate.credible ? (
              <dl className="grid grid-cols-2 gap-4 font-mono text-xs bg-elevated rounded-lg p-4 mb-6 border border-hairline">
                <div>
                  <dt className="text-ink-faint uppercase tracking-wider mb-1">95% Confidence Interval</dt>
                  <dd className="tabular-nums text-ink text-sm">
                    [{compact(estimate.ci_95[0])}, {compact(estimate.ci_95[1])}]
                  </dd>
                </div>
                <div>
                  <dt className="text-ink-faint uppercase tracking-wider mb-1">Sample Size</dt>
                  <dd className="tabular-nums text-ink text-sm">
                    {estimate.sample.clusters} clusters
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="mt-6 mb-6 text-sm leading-relaxed text-crit p-4 bg-crit/10 rounded-lg">
                Withheld. The difference in the data is real, but this design cannot attribute
                it to the intervention due to failed diagnostics.
              </p>
            )}

            <div className="space-y-4">
              <h3 className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted">Interpretation</h3>
              <p className="text-sm leading-relaxed text-ink">
                {estimate.interpretation}
              </p>
            </div>
            
            {estimate.caveats && estimate.caveats.length > 0 && (
              <div className="mt-6 pt-6 border-t border-hairline">
                <h3 className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted mb-3">Caveats</h3>
                <ul className="space-y-2 text-xs leading-relaxed text-ink-muted list-disc pl-4">
                  {estimate.caveats.map((caveat, i) => (
                    <li key={i}>{caveat}</li>
                  ))}
                </ul>
              </div>
            )}
          </section>

          <section className="space-y-4">
            <div className="rounded-2xl border border-hairline bg-surface p-6">
              <h2 className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted">
                Diagnostics
              </h2>
              <div className="mt-4 overflow-hidden rounded-lg border border-hairline">
                <table className="w-full text-left text-xs">
                  <thead className="bg-elevated text-ink-muted font-mono uppercase">
                    <tr>
                      <th className="px-3 py-2">Test</th>
                      <th className="px-3 py-2">Status</th>
                      <th className="px-3 py-2">p-value</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-hairline bg-surface">
                    {estimate.diagnostics.map((diagnostic) => (
                      <tr key={diagnostic.name}>
                        <td className="px-3 py-2 font-mono whitespace-nowrap" title={diagnostic.detail}>{diagnostic.name.replace(/_/g, " ")}</td>
                        <td className="px-3 py-2">
                          <span className={`px-2 py-0.5 rounded font-mono text-[10px] ${diagnostic.passed ? "bg-ok/20 text-ok" : "bg-crit/20 text-crit"}`}>
                            {diagnostic.passed ? "PASSED" : "FAILED"}
                          </span>
                        </td>
                        <td className="px-3 py-2 tabular-nums">{diagnostic.p_value.toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="rounded-2xl border border-hairline bg-surface p-6">
              <h2 className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted">
                Sample Info
              </h2>
              <dl className="mt-4 space-y-3 text-xs">
                <div>
                  <dt className="text-ink-faint mb-1">Treated Units</dt>
                  <dd className="font-mono bg-elevated p-2 rounded truncate" title={result.treated_units.join(", ")}>
                    {result.treated_units.join(", ")}
                  </dd>
                </div>
                {result.estimate?.sample?.control_units != null && (
                  <div>
                    <dt className="text-ink-faint mb-1">Control Units</dt>
                    <dd className="font-mono bg-elevated p-2 rounded truncate">
                      {result.estimate.sample.control_units}
                    </dd>
                  </div>
                )}
                {result.panel && result.panel.note && (
                  <p className="mt-4 border-t border-hairline pt-3 font-mono text-[11px] leading-relaxed text-ink-faint">
                    {result.panel.note}
                  </p>
                )}
              </dl>
            </div>
          </section>
        </div>
      ) : null}
    </Shell>
  );
}
