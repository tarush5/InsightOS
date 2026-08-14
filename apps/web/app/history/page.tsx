"use client";

import { useCallback, useEffect, useState } from "react";
import { EmptyState, ErrorNote, PageHeading, Shell, SignInRequired } from "@/components/Shell";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { HistoryRow } from "@/lib/types";

const PAGE_SIZE = 25;

function confidenceTone(value: number | null) {
  if (value === null) return "text-ink-faint";
  if (value >= 0.75) return "text-ok";
  if (value >= 0.5) return "text-cyan";
  if (value >= 0.35) return "text-warn";
  return "text-crit";
}

function relativeTime(iso: string | null) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export default function HistoryPage() {
  const { status } = useSession();
  const [rows, setRows] = useState<HistoryRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (next: number) => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.history(PAGE_SIZE, next);
      setRows(data.investigations);
      setTotal(data.total);
      setOffset(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load history.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (status === "signed-in") void load(0);
  }, [status, load]);

  if (status !== "signed-in") {
    return (
      <Shell>
        <SignInRequired what="see past investigations" />
      </Shell>
    );
  }

  return (
    <Shell>
      <PageHeading eyebrow="Audit" title="Investigation history">
        Every run is stored with the stage timeline and the evidence each conclusion rested
        on, so an answer can be re-read months later. The verdict column is the critic&apos;s,
        not the model&apos;s: <span className="font-mono text-ink">flagged</span> means a claim
        in the narrative could not be matched to a computed figure.
      </PageHeading>

      {error ? <ErrorNote message={error} fix="Check that the API is reachable." /> : null}

      {!error && rows.length === 0 && !loading ? (
        <EmptyState
          title="No investigations yet"
          body="Ask a question on the Investigate page and it will appear here with its full reasoning trace."
        />
      ) : null}

      {rows.length > 0 ? (
        <div className="overflow-hidden rounded-2xl border border-hairline">
          <table className="w-full border-collapse text-left text-sm">
            <caption className="sr-only">Past investigations, newest first</caption>
            <thead>
              <tr className="border-b border-hairline bg-surface font-mono text-[11px] uppercase tracking-[0.12em] text-ink-muted">
                <th scope="col" className="px-4 py-3 font-medium">Reference</th>
                <th scope="col" className="px-4 py-3 font-medium">Question</th>
                <th scope="col" className="px-4 py-3 font-medium">Verdict</th>
                <th scope="col" className="px-4 py-3 text-right font-medium">Confidence</th>
                <th scope="col" className="px-4 py-3 text-right font-medium">Took</th>
                <th scope="col" className="px-4 py-3 text-right font-medium">When</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.reference} className="border-b border-hairline/60 last:border-0 hover:bg-elevated/40">
                  <td className="px-4 py-3 font-mono text-xs text-cyan">{row.reference}</td>
                  <td className="px-4 py-3">
                    <p className="text-ink">{row.question}</p>
                    {row.headline ? (
                      <p className="mt-0.5 text-xs text-ink-muted">{row.headline}</p>
                    ) : null}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-md px-2 py-0.5 font-mono text-[11px] ${
                        row.verdict === "approved"
                          ? "bg-ok/15 text-ok"
                          : row.status === "failed"
                            ? "bg-crit/15 text-crit"
                            : "bg-warn/15 text-warn"
                      }`}
                    >
                      {row.verdict ?? row.status}
                    </span>
                  </td>
                  <td className={`px-4 py-3 text-right font-mono text-xs ${confidenceTone(row.confidence)}`}>
                    {row.confidence === null ? "—" : `${Math.round(row.confidence * 100)}%`}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs text-ink-faint">
                    {row.duration_ms === null ? "—" : `${row.duration_ms}ms`}
                  </td>
                  <td className="px-4 py-3 text-right font-mono text-xs text-ink-faint">
                    {relativeTime(row.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {total > PAGE_SIZE ? (
        <div className="mt-4 flex items-center justify-between font-mono text-xs text-ink-muted">
          <span>
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
          </span>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={offset === 0 || loading}
              onClick={() => void load(Math.max(0, offset - PAGE_SIZE))}
              className="rounded-lg border border-hairline px-3 py-1.5 disabled:opacity-40 hover:border-ink-faint"
            >
              Previous
            </button>
            <button
              type="button"
              disabled={offset + PAGE_SIZE >= total || loading}
              onClick={() => void load(offset + PAGE_SIZE)}
              className="rounded-lg border border-hairline px-3 py-1.5 disabled:opacity-40 hover:border-ink-faint"
            >
              Next
            </button>
          </div>
        </div>
      ) : null}
    </Shell>
  );
}
