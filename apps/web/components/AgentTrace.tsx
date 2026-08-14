"use client";

import { motion } from "framer-motion";
import clsx from "clsx";
import type { InvestigationEvent, StageState } from "@/lib/types";

/**
 * The signature element of the product.
 *
 * Rather than a generic progress bar, the trace is an instrument rail: each
 * stage is a row on a vertical bus, the active row carries a travelling
 * scanline, and completed rows keep their measured latency visible. The point
 * is that the user can watch the system reason and can audit how long each
 * step actually took -- progress here is evidence, not decoration.
 */

const STAGE_ORDER = [
  "understand", "plan", "resolve_metric", "build_query", "validate_sql",
  "execute", "profile", "anomaly", "root_cause", "significance",
  "forecast", "recommend", "verify", "complete",
] as const;

const STAGE_LABELS: Record<string, string> = {
  understand: "Understanding the question",
  plan: "Building an analysis plan",
  resolve_metric: "Resolving the governed metric",
  build_query: "Generating SQL",
  validate_sql: "Validating SQL",
  execute: "Executing query",
  profile: "Profiling the result set",
  anomaly: "Detecting anomalies",
  root_cause: "Investigating root causes",
  significance: "Testing statistical significance",
  forecast: "Forecasting forward",
  recommend: "Drafting recommendations",
  verify: "Verifying against evidence",
  complete: "Investigation complete",
};

function Glyph({ state }: { state: StageState }) {
  if (state === "done")
    return <span className="text-ok" aria-hidden>✓</span>;
  if (state === "failed")
    return <span className="text-crit" aria-hidden>✕</span>;
  if (state === "skipped")
    return <span className="text-ink-faint" aria-hidden>—</span>;
  if (state === "running")
    return <span className="block h-2 w-2 rounded-full bg-cyan animate-pulseSoft" aria-hidden />;
  return <span className="block h-2 w-2 rounded-full border border-hairline" aria-hidden />;
}

export function AgentTrace({ events }: { events: InvestigationEvent[] }) {
  const byStage = new Map<string, InvestigationEvent>();
  for (const e of events) {
    const prior = byStage.get(e.stage);
    // A stage may emit running then done; the later state always wins.
    if (!prior || e.elapsed_ms >= prior.elapsed_ms) byStage.set(e.stage, e);
  }
  const latest = events.at(-1);

  return (
    <section className="panel p-5" aria-label="Agent progress">
      <div className="mb-4 flex items-baseline justify-between">
        <h2 className="label-mono">Agent trace</h2>
        {latest && (
          <span className="font-mono text-xs text-ink-muted" role="status" aria-live="polite">
            {Math.round(latest.progress * 100)}% · {(latest.elapsed_ms / 1000).toFixed(1)}s
          </span>
        )}
      </div>

      <ol className="relative space-y-0.5">
        {/* The bus line the whole rail hangs from. */}
        <div className="absolute left-[7px] top-2 bottom-2 w-px bg-hairline" aria-hidden />

        {STAGE_ORDER.map((stage, i) => {
          const event = byStage.get(stage);
          const state: StageState = event?.state ?? "pending";
          const active = state === "running";

          return (
            <motion.li
              key={stage}
              initial={{ opacity: 0, x: -4 }}
              animate={{ opacity: state === "pending" ? 0.4 : 1, x: 0 }}
              transition={{ duration: 0.24, delay: Math.min(i * 0.015, 0.2) }}
              className={clsx(
                "relative flex items-center gap-3 overflow-hidden rounded-lg px-2 py-2",
                active && "bg-cyan/5",
              )}
            >
              {active && (
                <span
                  className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-cyan to-transparent animate-scanline"
                  aria-hidden
                />
              )}
              <span className="z-10 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-base text-xs">
                <Glyph state={state} />
              </span>
              <span
                className={clsx(
                  "flex-1 text-sm",
                  active ? "text-ink" : state === "done" ? "text-ink-muted" : "text-ink-faint",
                )}
              >
                {STAGE_LABELS[stage] ?? stage}
              </span>
              {event && state !== "pending" && (
                <span className="font-mono text-[0.6875rem] tabular-nums text-ink-faint">
                  {(event.elapsed_ms / 1000).toFixed(2)}s
                </span>
              )}
            </motion.li>
          );
        })}
      </ol>
    </section>
  );
}
