"use client";

import clsx from "clsx";
import type { Confidence, CriticCheck } from "@/lib/types";

const COMPONENT_COPY: Record<string, string> = {
  data: "Completeness, freshness and volume of the queried result",
  statistical: "Whether the change exceeds normal variation",
  model: "Backtested accuracy of the forecast being relied on",
  reasoning: "Share of verification checks the conclusion passed",
};

function tone(value: number) {
  if (value >= 0.8) return "text-ok";
  if (value >= 0.6) return "text-cyan";
  if (value >= 0.4) return "text-warn";
  return "text-crit";
}

function barTone(value: number) {
  if (value >= 0.8) return "bg-ok";
  if (value >= 0.6) return "bg-cyan";
  if (value >= 0.4) return "bg-warn";
  return "bg-crit";
}

/**
 * Confidence is shown decomposed, with the weakest component named. A single
 * blended percentage invites false trust; naming the limiting factor tells the
 * reader exactly which part of the answer to go and check.
 */
export function ConfidencePanel({
  confidence,
  critic,
}: {
  confidence: Confidence;
  critic?: { approved: boolean; passed: number; total: number; checks: CriticCheck[] };
}) {
  const parts = ["data", "statistical", "model", "reasoning"] as const;

  return (
    <section className="panel p-5" aria-label="Confidence breakdown">
      <h2 className="label-mono mb-4">Confidence</h2>

      <div className="mb-5 flex items-end gap-3">
        <span className={clsx("font-display text-5xl font-bold tabular-nums", tone(confidence.overall))}>
          {Math.round(confidence.overall * 100)}
          <span className="text-2xl">%</span>
        </span>
        <span className="mb-2 font-mono text-xs uppercase tracking-widest text-ink-muted">
          {confidence.label}
        </span>
      </div>

      <p className="mb-5 rounded-lg border border-hairline bg-base/60 px-3 py-2 text-sm text-ink-muted">
        Limited by <span className="text-ink">{confidence.limiting_factor}</span> confidence —{" "}
        {COMPONENT_COPY[confidence.limiting_factor]}.
      </p>

      <dl className="space-y-3">
        {parts.map((key) => (
          <div key={key}>
            <div className="mb-1 flex items-baseline justify-between">
              <dt className="label-mono">{key}</dt>
              <dd className={clsx("font-mono text-xs tabular-nums", tone(confidence[key]))}>
                {Math.round(confidence[key] * 100)}%
              </dd>
            </div>
            <div
              className="h-1 overflow-hidden rounded-full bg-hairline"
              role="meter"
              aria-valuenow={Math.round(confidence[key] * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label={`${key} confidence`}
            >
              <div
                className={clsx("h-full rounded-full transition-[width] duration-500", barTone(confidence[key]))}
                style={{ width: `${confidence[key] * 100}%` }}
              />
            </div>
          </div>
        ))}
      </dl>

      {critic && (
        <details className="mt-5 border-t border-hairline pt-4">
          <summary className="cursor-pointer text-sm text-ink-muted hover:text-ink">
            Verification: {critic.passed}/{critic.total} checks passed
          </summary>
          <ul className="mt-3 space-y-2">
            {critic.checks.map((check) => (
              <li key={check.name} className="flex gap-2 text-xs">
                <span className={check.passed ? "text-ok" : "text-crit"} aria-hidden>
                  {check.passed ? "✓" : "✕"}
                </span>
                <span>
                  <span className="font-mono text-ink">{check.name}</span>
                  <span className="block text-ink-faint">{check.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
