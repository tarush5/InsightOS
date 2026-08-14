"use client";

import { useCallback, useEffect, useState } from "react";
import { EmptyState, ErrorNote, PageHeading, Shell, SignInRequired } from "@/components/Shell";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { AskResult, DataSourceSummary } from "@/lib/types";

interface GenerationFailure {
  message: string;
  degraded: boolean;
  attempts: { sql: string; ok: boolean; errors: string[] }[];
}

export default function QueryPage() {
  const { status } = useSession();
  const [sources, setSources] = useState<DataSourceSummary[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResult | null>(null);
  const [failure, setFailure] = useState<GenerationFailure | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await api.dataSources();
      setSources(data.data_sources);
      if (data.data_sources.length > 0 && !sourceId) {
        setSourceId(data.data_sources[0]!.id);
      }
    } catch {
      /* the page still explains itself with no sources connected */
    }
  }, [sourceId]);

  useEffect(() => {
    if (status === "signed-in") void load();
  }, [status, load]);

  const run = async (execute: boolean) => {
    if (!sourceId || question.trim().length < 5) return;
    setBusy(true);
    setError(null);
    setFailure(null);
    setResult(null);
    try {
      setResult(
        execute
          ? await api.askQuery(question, sourceId)
          : await api.explainQuery(question, sourceId),
      );
    } catch (err) {
      if (err instanceof ApiError && err.detail && typeof err.detail === "object") {
        const detail = err.detail as Record<string, unknown>;
        setFailure({
          message: String(detail.message ?? err.message),
          degraded: Boolean(detail.degraded),
          attempts: (detail.attempts as GenerationFailure["attempts"]) ?? [],
        });
      } else {
        setError(err instanceof ApiError ? err.message : "The query could not be run.");
      }
    } finally {
      setBusy(false);
    }
  };

  if (status !== "signed-in") {
    return (
      <Shell>
        <SignInRequired what="run an ad-hoc query" />
      </Shell>
    );
  }

  return (
    <Shell>
      <PageHeading eyebrow="Ad-hoc" title="Ask anything">
        For questions the semantic layer has no metric for. A model writes the SQL and the
        validator checks it against the crawled schema before anything runs — so the query
        is shown to you either way, and you can generate it without executing it.
      </PageHeading>

      {sources.length === 0 ? (
        <EmptyState
          title="No data source connected"
          body="Register a source and crawl its schema first. The generator is given the crawled catalog and nothing else, so without one it can only produce rejected queries."
        />
      ) : (
        <div className="rounded-2xl border border-hairline bg-surface p-6">
          <label
            htmlFor="source"
            className="block font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted"
          >
            Data source
          </label>
          <select
            id="source"
            value={sourceId}
            onChange={(e) => setSourceId(e.target.value)}
            className="mt-1.5 w-full rounded-lg border border-hairline bg-elevated px-3 py-2 font-mono text-xs text-ink focus:border-cyan-dim focus:outline-none"
          >
            {sources.map((source) => (
              <option key={source.id} value={source.id}>
                {source.name} · {source.kind} · {source.health}
              </option>
            ))}
          </select>

          <label
            htmlFor="question"
            className="mt-5 block font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted"
          >
            Question
          </label>
          <textarea
            id="question"
            rows={3}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Which regions had the highest average order value in August?"
            className="mt-1.5 w-full resize-none rounded-lg border border-hairline bg-elevated px-3 py-2.5 text-sm text-ink placeholder:text-ink-faint focus:border-cyan-dim focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan/40"
          />

          <div className="mt-5 flex gap-2">
            <button
              type="button"
              onClick={() => void run(false)}
              disabled={busy}
              className="rounded-lg border border-hairline px-4 py-2 font-mono text-xs text-ink-muted transition-colors hover:border-ink-faint hover:text-ink disabled:opacity-50"
            >
              Show the SQL only
            </button>
            <button
              type="button"
              onClick={() => void run(true)}
              disabled={busy}
              className="rounded-lg bg-cyan px-4 py-2 font-mono text-xs font-medium text-base transition-opacity hover:opacity-90 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan"
            >
              {busy ? "Working…" : "Run it"}
            </button>
          </div>
        </div>
      )}

      {error ? (
        <div className="mt-6">
          <ErrorNote message={error} />
        </div>
      ) : null}

      {/* A failed generation shows every rejected attempt with the validator's
          reason. Hiding them would make the failure look arbitrary. */}
      {failure ? (
        <section className="mt-6 rounded-2xl border border-warn/40 bg-warn/5 p-6">
          <h2 className="font-display text-base font-semibold">No valid query</h2>
          <p className="mt-2 max-w-prose text-sm text-ink-muted">{failure.message}</p>
          {failure.attempts.length > 0 ? (
            <ol className="mt-5 space-y-4">
              {failure.attempts.map((attempt, index) => (
                <li key={index}>
                  <p className="font-mono text-[11px] uppercase tracking-[0.12em] text-ink-faint">
                    Attempt {index + 1}
                  </p>
                  <pre className="mt-1.5 overflow-x-auto rounded-lg border border-hairline bg-elevated p-3 font-mono text-[11px] leading-relaxed text-ink-muted">
                    {attempt.sql || "(no SQL returned)"}
                  </pre>
                  <ul className="mt-1.5 space-y-1">
                    {attempt.errors.map((message) => (
                      <li key={message} className="font-mono text-[11px] text-warn">
                        {message}
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ol>
          ) : null}
        </section>
      ) : null}

      {result ? (
        <section className="mt-6 space-y-4">
          {/* SQL first, deliberately: the right first response to a query a
              model wrote is to read it. */}
          <div className="rounded-2xl border border-hairline bg-surface p-6">
            <div className="flex items-baseline justify-between gap-4">
              <h2 className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted">
                The query that {result.executed === false ? "would run" : "ran"}
              </h2>
              {typeof result.attempts === "number" && result.attempts > 1 ? (
                <span className="font-mono text-[11px] text-warn">
                  repaired after {result.attempts - 1}{" "}
                  {result.attempts === 2 ? "rejection" : "rejections"}
                </span>
              ) : null}
            </div>
            <pre className="mt-3 overflow-x-auto rounded-lg border border-hairline bg-elevated p-4 font-mono text-[11px] leading-relaxed text-ink">
              {result.sql}
            </pre>
            <p className="mt-3 text-xs leading-relaxed text-ink-faint">{result.provenance}</p>
          </div>

          {result.rows && result.rows.length > 0 ? (
            <div className="overflow-hidden rounded-2xl border border-hairline">
              <table className="w-full border-collapse text-left text-sm">
                <caption className="sr-only">Query results</caption>
                <thead>
                  <tr className="border-b border-hairline bg-surface font-mono text-[11px] uppercase tracking-[0.12em] text-ink-muted">
                    {(result.columns ?? []).map((column) => (
                      <th key={column} scope="col" className="px-4 py-3 font-medium">
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.slice(0, 100).map((row, index) => (
                    <tr key={index} className="border-b border-hairline/60 last:border-0">
                      {(result.columns ?? []).map((column) => (
                        <td key={column} className="px-4 py-2.5 font-mono text-xs tabular-nums">
                          {String(row[column] ?? "—")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {result.warnings && result.warnings.length > 0 ? (
            <ul className="space-y-1.5">
              {result.warnings.map((warning) => (
                <li key={warning} className="font-mono text-[11px] text-warn">
                  {warning}
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ) : null}
    </Shell>
  );
}
