"use client";

import { useCallback, useEffect, useState } from "react";
import { EmptyState, ErrorNote, PageHeading, Shell, SignInRequired } from "@/components/Shell";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { AlertPreview, AlertSummary } from "@/lib/types";

// `as const` so the first element is a known string under
// noUncheckedIndexedAccess, rather than string | undefined.
const EXAMPLES = [
  "Alert me when revenue drops more than 15% in 7 days",
  "Tell me if support first response hours rises above 8",
  "Alert on unusual revenue",
] as const;

export default function AlertsPage() {
  const { status } = useSession();
  const [text, setText] = useState<string>(EXAMPLES[0]);
  const [name, setName] = useState("");
  const [preview, setPreview] = useState<AlertPreview | null>(null);
  const [missing, setMissing] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [alerts, setAlerts] = useState<AlertSummary[]>([]);
  const [busy, setBusy] = useState(false);

  const loadAlerts = useCallback(async () => {
    try {
      setAlerts((await api.alerts()).alerts);
    } catch {
      /* the list is secondary; the compile form still works */
    }
  }, []);

  useEffect(() => {
    if (status === "signed-in") void loadAlerts();
  }, [status, loadAlerts]);

  const compile = async () => {
    setBusy(true);
    setError(null);
    setMissing([]);
    setPreview(null);
    try {
      setPreview(await api.previewAlert(text));
    } catch (err) {
      if (err instanceof ApiError) {
        const detail = err.detail as { missing?: string[] } | undefined;
        setMissing(detail?.missing ?? []);
        setError(err.message);
      } else {
        setError("Could not compile the alert.");
      }
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!preview) return;
    setBusy(true);
    try {
      await api.createAlert(name.trim() || preview.readback.slice(0, 60), text);
      setPreview(null);
      setName("");
      await loadAlerts();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save the alert.");
    } finally {
      setBusy(false);
    }
  };

  if (status !== "signed-in") {
    return (
      <Shell>
        <SignInRequired what="manage alerts" />
      </Shell>
    );
  }

  const backtest = preview?.backtest;

  return (
    <Shell>
      <PageHeading eyebrow="Monitoring" title="Alerts">
        Describe the alert in your own words. It compiles to a structured rule you can read
        back, and gets replayed over history first — so an alert that would have paged you
        every other day is discovered here, not at 3am.
      </PageHeading>

      <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        <section className="rounded-2xl border border-hairline bg-surface p-6">
          <label
            htmlFor="alert-text"
            className="block font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted"
          >
            Alert in plain words
          </label>
          <textarea
            id="alert-text"
            rows={3}
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="mt-2 w-full resize-none rounded-lg border border-hairline bg-elevated px-3 py-2.5 text-sm text-ink placeholder:text-ink-faint focus:border-cyan-dim focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan/40"
          />

          <div className="mt-3 flex flex-wrap gap-2">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => setText(example)}
                className="rounded-lg border border-hairline px-2.5 py-1 font-mono text-[11px] text-ink-muted transition-colors hover:border-cyan-dim hover:text-cyan"
              >
                {example.length > 42 ? `${example.slice(0, 42)}…` : example}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={() => void compile()}
            disabled={busy || text.trim().length < 5}
            className="mt-5 rounded-lg bg-cyan px-4 py-2 font-mono text-xs font-medium text-base transition-opacity hover:opacity-90 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan"
          >
            {busy ? "Checking…" : "Check this alert"}
          </button>

          {error ? (
            <div className="mt-4">
              <ErrorNote
                message={error}
                fix={
                  missing.length
                    ? `Add the missing ${missing.join(" and ")} and try again.`
                    : undefined
                }
              />
            </div>
          ) : null}
        </section>

        <section className="rounded-2xl border border-hairline bg-surface p-6">
          <h2 className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted">
            What it will do
          </h2>

          {!preview ? (
            <p className="mt-4 text-sm text-ink-faint">
              The compiled rule and its firing history appear here before anything is saved.
            </p>
          ) : (
            <>
              <p className="mt-3 text-sm leading-relaxed text-ink">{preview.readback}</p>

              <dl className="mt-4 grid grid-cols-2 gap-3 font-mono text-[11px]">
                <div>
                  <dt className="text-ink-faint">Condition</dt>
                  <dd className="mt-0.5 text-ink">{preview.rule.condition}</dd>
                </div>
                <div>
                  <dt className="text-ink-faint">Cooldown</dt>
                  <dd className="mt-0.5 text-ink">{preview.rule.cooldown_hours}h</dd>
                </div>
              </dl>

              {backtest?.available ? (
                <div
                  className={`mt-5 rounded-xl border px-4 py-3 ${
                    backtest.noisy ? "border-warn/40 bg-warn/10" : "border-hairline bg-elevated"
                  }`}
                >
                  <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-muted">
                    Replayed over {backtest.history_days} days
                  </p>
                  <p className="mt-1.5 text-sm text-ink">
                    Would have fired{" "}
                    <span className="font-mono text-cyan">{backtest.would_have_fired}</span> times
                    — about{" "}
                    <span className={`font-mono ${backtest.noisy ? "text-warn" : "text-ink"}`}>
                      {backtest.estimated_per_month}
                    </span>{" "}
                    per month.
                  </p>
                  {backtest.noisy ? (
                    <p className="mt-2 text-xs text-warn">
                      That is often enough that people stop reading it. Consider a larger
                      threshold or a longer window.
                    </p>
                  ) : null}
                  {backtest.warehouse_is_stale ? (
                    <p className="mt-2 font-mono text-[11px] text-ink-faint">
                      Anchored on {backtest.anchored_on}; the warehouse has no newer data.
                    </p>
                  ) : null}
                </div>
              ) : (
                <p className="mt-5 text-xs text-ink-faint">
                  No history to replay against: {backtest?.reason}
                </p>
              )}

              <div className="mt-5 flex gap-2">
                <input
                  aria-label="Alert name"
                  placeholder="Name this alert"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="flex-1 rounded-lg border border-hairline bg-elevated px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-cyan-dim focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => void save()}
                  disabled={busy}
                  className="rounded-lg border border-cyan-dim px-4 py-2 font-mono text-xs text-cyan transition-colors hover:bg-cyan-dim/20 disabled:opacity-50"
                >
                  Save alert
                </button>
              </div>
            </>
          )}
        </section>
      </div>

      <h2 className="mt-12 font-display text-lg font-bold tracking-tight">Active alerts</h2>
      <div className="mt-4">
        {alerts.length === 0 ? (
          <EmptyState
            title="Nothing is being watched yet"
            body="Saved alerts run hourly in the background and appear here with the time they last fired."
          />
        ) : (
          <ul className="space-y-2">
            {alerts.map((alert) => (
              <li
                key={alert.id}
                className="flex items-center gap-4 rounded-xl border border-hairline bg-surface px-4 py-3"
              >
                <span
                  aria-hidden
                  className={`h-2 w-2 shrink-0 rounded-full ${alert.is_active ? "bg-ok" : "bg-ink-faint"}`}
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-ink">{alert.name}</p>
                  <p className="truncate font-mono text-[11px] text-ink-faint">
                    {alert.natural_language}
                  </p>
                </div>
                <span className="font-mono text-[11px] text-ink-muted">
                  {alert.last_triggered_at
                    ? `fired ${new Date(alert.last_triggered_at).toLocaleDateString()}`
                    : "never fired"}
                </span>
                <button
                  type="button"
                  onClick={async () => {
                    await api.setAlertActive(alert.id, !alert.is_active);
                    await loadAlerts();
                  }}
                  className="rounded-lg border border-hairline px-3 py-1.5 font-mono text-[11px] text-ink-muted transition-colors hover:border-ink-faint hover:text-ink"
                >
                  {alert.is_active ? "Pause" : "Resume"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Shell>
  );
}
