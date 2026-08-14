"use client";

import clsx from "clsx";
import type { Driver } from "@/lib/types";

const pct = (v: number) => `${v > 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;

/**
 * Signed contribution bars on a shared zero axis. Every bar is a measured share
 * of the total change, so bar widths are directly comparable and sum to the
 * headline figure -- which is why the axis is centred rather than left-aligned.
 */
export function DriverChart({ drivers }: { drivers: Driver[] }) {
  if (drivers.length === 0) {
    return (
      <section className="panel p-5">
        <h2 className="label-mono mb-2">Drivers</h2>
        <p className="text-sm text-ink-muted">
          No segment contributed more than 0.5% of the total change. This change
          is broad-based rather than concentrated.
        </p>
      </section>
    );
  }

  const max = Math.max(...drivers.map((d) => Math.abs(d.contribution_pct)));

  return (
    <section className="panel p-5" aria-label="Contribution by segment">
      <div className="mb-4 flex items-baseline justify-between">
        <h2 className="label-mono">Drivers</h2>
        <span className="font-mono text-[0.6875rem] text-ink-faint">
          share of total change
        </span>
      </div>

      <ul className="space-y-3">
        {drivers.map((d) => {
          const width = (Math.abs(d.contribution_pct) / max) * 50;
          const negative = d.contribution_pct < 0;
          return (
            <li key={`${d.dimension}-${d.segment}`}>
              <div className="mb-1 flex items-baseline justify-between gap-3">
                <span className="truncate text-sm">
                  <span className="text-ink">{d.segment}</span>{" "}
                  <span className="label-mono">{d.dimension}</span>
                </span>
                <span
                  className={clsx(
                    "shrink-0 font-mono text-xs tabular-nums",
                    negative ? "text-crit" : "text-ok",
                  )}
                >
                  {pct(d.contribution_pct)}
                </span>
              </div>

              <div className="relative h-2 rounded-full bg-base">
                <div className="absolute left-1/2 top-0 h-full w-px bg-hairline" aria-hidden />
                <div
                  className={clsx(
                    "absolute top-0 h-full transition-all duration-500",
                    negative ? "rounded-l-full bg-crit/70" : "rounded-r-full bg-ok/70",
                  )}
                  style={
                    negative
                      ? { right: "50%", width: `${width}%` }
                      : { left: "50%", width: `${width}%` }
                  }
                />
              </div>

              {d.segment_pct_change !== null && (
                <p className="mt-1 text-xs text-ink-faint">
                  {pct(d.segment_pct_change)} within this segment
                  {d.status !== "changed" && ` · ${d.status}`}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
