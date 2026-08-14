"use client";

import { useCallback, useEffect, useState } from "react";
import { EmptyState, ErrorNote, PageHeading, Shell, SignInRequired } from "@/components/Shell";
import { api, ApiError } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { DocumentRecord, DocumentSearchResult } from "@/lib/types";

export default function DocumentsPage() {
  const { status } = useSession();
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<DocumentSearchResult | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setDocuments((await api.documents()).documents);
    } catch {
      /* the upload form still works with no list */
    }
  }, []);

  useEffect(() => {
    if (status === "signed-in") void load();
  }, [status, load]);

  const ingest = async () => {
    setBusy(true);
    setError(null);
    setWarning(null);
    try {
      const record = await api.ingestDocument(title.trim() || "Untitled", text);
      if (record.warning) setWarning(record.warning);
      setTitle("");
      setText("");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not add the document.");
    } finally {
      setBusy(false);
    }
  };

  const search = async () => {
    setBusy(true);
    setError(null);
    setResults(null);
    try {
      setResults(await api.searchDocuments(query));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Search failed.");
    } finally {
      setBusy(false);
    }
  };

  if (status !== "signed-in") {
    return (
      <Shell>
        <SignInRequired what="work with documents" />
      </Shell>
    );
  }

  return (
    <Shell>
      <PageHeading eyebrow="Knowledge" title="Documents">
        For questions whose answer lives in a policy, a postmortem or a contract rather than
        in the warehouse. Answers are quotations with their source, because a passage is not
        a computed figure and should not be presented as one.
      </PageHeading>

      <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
        <section className="rounded-2xl border border-hairline bg-surface p-6">
          <h2 className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted">
            Add a document
          </h2>
          <input
            aria-label="Document title"
            placeholder="Refund Policy"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="mt-3 w-full rounded-lg border border-hairline bg-elevated px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-cyan-dim focus:outline-none"
          />
          <textarea
            aria-label="Document text"
            rows={8}
            placeholder={"# Refund Policy\n\n## Escalations\nRefunds above 500 GBP require finance approval."}
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="mt-3 w-full resize-none rounded-lg border border-hairline bg-elevated px-3 py-2.5 font-mono text-xs leading-relaxed text-ink placeholder:text-ink-faint focus:border-cyan-dim focus:outline-none"
          />
          <p className="mt-2 font-mono text-[11px] text-ink-faint">
            Markdown headings are used as chunk boundaries, so a clause keeps the section it
            belongs to.
          </p>
          <button
            type="button"
            onClick={() => void ingest()}
            disabled={busy || text.trim().length < 10}
            className="mt-4 rounded-lg bg-cyan px-4 py-2 font-mono text-xs font-medium text-base transition-opacity hover:opacity-90 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan"
          >
            {busy ? "Indexing…" : "Add document"}
          </button>

          {/* Shown at upload, not at retrieval: the uploader is the person who
              can say whether the file should contain that text. */}
          {warning ? (
            <div
              role="alert"
              className="mt-4 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3"
            >
              <p className="text-sm text-ink">{warning}</p>
            </div>
          ) : null}
        </section>

        <section className="rounded-2xl border border-hairline bg-surface p-6">
          <h2 className="font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted">
            Search
          </h2>
          <input
            aria-label="Search query"
            placeholder="who approves refunds above 500"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void search();
            }}
            className="mt-3 w-full rounded-lg border border-hairline bg-elevated px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-cyan-dim focus:outline-none"
          />
          <button
            type="button"
            onClick={() => void search()}
            disabled={busy || query.trim().length < 2 || documents.length === 0}
            className="mt-3 rounded-lg border border-cyan-dim px-4 py-2 font-mono text-xs text-cyan transition-colors hover:bg-cyan-dim/20 disabled:opacity-50"
          >
            Find passages
          </button>

          {results ? (
            <>
              {results.retrieval.degraded ? (
                <p className="mt-4 font-mono text-[11px] leading-relaxed text-warn">
                  {results.retrieval.note}
                </p>
              ) : null}
              <ol className="mt-4 space-y-4">
                {results.results.map((hit) => (
                  <li key={hit.chunk_id}>
                    <p className="font-mono text-[11px] text-cyan">
                      {hit.heading_path.join(" › ") || "(no heading)"}
                    </p>
                    <p className="mt-1 text-sm leading-relaxed text-ink">{hit.text}</p>
                    {/* Rank, never a percentage: a fused RRF score has no
                        reading as a probability. */}
                    <p className="mt-1 font-mono text-[11px] text-ink-faint">
                      rank {hit.rank} · matched by {hit.matched_by.join(", ")} ·{" "}
                      {hit.chunk_id.slice(0, 8)}
                    </p>
                  </li>
                ))}
              </ol>
              {results.excluded.length > 0 ? (
                <p className="mt-4 font-mono text-[11px] leading-relaxed text-warn">
                  {results.excluded.length} passage(s) were withheld: they contain
                  instructions addressed to an AI system rather than material to cite.
                </p>
              ) : null}
              {results.results.length === 0 && results.excluded.length === 0 ? (
                <p className="mt-4 text-sm text-ink-faint">Nothing matched that query.</p>
              ) : null}
            </>
          ) : null}
        </section>
      </div>

      {error ? (
        <div className="mt-6">
          <ErrorNote message={error} />
        </div>
      ) : null}

      <h2 className="mt-12 font-display text-lg font-bold tracking-tight">Indexed</h2>
      <div className="mt-4">
        {documents.length === 0 ? (
          <EmptyState
            title="No documents yet"
            body="Add a policy, a runbook or a postmortem and it becomes searchable with citations back to the section it came from."
          />
        ) : (
          <ul className="space-y-2">
            {documents.map((doc) => (
              <li
                key={doc.document_id}
                className="flex items-center gap-4 rounded-xl border border-hairline bg-surface px-4 py-3"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-ink">{doc.title}</p>
                  <p className="font-mono text-[11px] text-ink-faint">
                    {doc.chunk_count} chunks · {doc.char_count.toLocaleString()} chars
                  </p>
                </div>
                {doc.flagged_chunks > 0 ? (
                  <span className="rounded-md bg-warn/15 px-2 py-0.5 font-mono text-[11px] text-warn">
                    {doc.flagged_chunks} flagged
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Shell>
  );
}
