"use client";

import { useEffect, useState } from "react";
import { Shell, SignInRequired, PageHeading, EmptyState } from "../../components/Shell";
import { useSession } from "../../lib/session";
import { api } from "../../lib/api";
import type { DataSourceSummary } from "../../lib/types";
import { useRouter } from "next/navigation";

export default function DatasetsPage() {
  const { status } = useSession();

  if (status === "unknown") return <Shell><div className="animate-pulseSoft">Loading...</div></Shell>;
  if (status === "signed-out") return <Shell><SignInRequired what="explore datasets" /></Shell>;

  return <DatasetsContent />;
}

function DatasetsContent() {
  const [sources, setSources] = useState<DataSourceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [question, setQuestion] = useState("");
  const [expandedSource, setExpandedSource] = useState<string | null>(null);
  const router = useRouter();

  useEffect(() => {
    api.dataSources()
      .then((res) => {
        setSources(res.data_sources);
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }, []);

  const handleAsk = (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim()) return;
    router.push(`/query?q=${encodeURIComponent(question)}`);
  };

  if (loading) {
    return (
      <Shell>
        <PageHeading eyebrow="Discovery" title="Dataset Explorer" />
        <div className="animate-pulseSoft text-ink-muted">Loading datasets...</div>
      </Shell>
    );
  }

  return (
    <Shell>
      <PageHeading eyebrow="Discovery" title="Dataset Explorer">
        Datasets are discovered through connected data sources. Explore tables and columns from your catalog.
      </PageHeading>

      {sources.length === 0 ? (
        <EmptyState 
          title="No data sources available" 
          body="Connect a data source first to explore datasets." 
        />
      ) : (
        <div className="grid gap-6">
          <form onSubmit={handleAsk} className="flex gap-2">
            <input
              type="text"
              placeholder="Ask AI about this dataset..."
              className="flex-1 rounded-lg border border-hairline bg-surface px-4 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-cyan focus:outline-none focus:ring-1 focus:ring-cyan"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
            />
            <button
              type="submit"
              className="rounded-lg bg-cyan px-4 py-2 text-sm font-medium text-base transition-opacity hover:opacity-90"
            >
              Ask
            </button>
          </form>

          <div className="grid gap-4">
            {sources.map((source) => (
              <div key={source.id} className="panel">
                <button
                  className="flex w-full items-center justify-between text-left"
                  onClick={() => setExpandedSource(expandedSource === source.id ? null : source.id)}
                >
                  <div className="flex flex-col">
                    <span className="font-display font-semibold">{source.name}</span>
                    <span className="text-xs text-ink-muted">{source.kind}</span>
                  </div>
                  <span className="text-ink-faint">{expandedSource === source.id ? "−" : "+"}</span>
                </button>
                
                {expandedSource === source.id && (
                  <div className="mt-4 border-t border-hairline pt-4">
                    <p className="text-xs text-ink-muted mb-2">Discovered Tables:</p>
                    <div className="flex flex-wrap gap-2">
                      {/* Placeholder for table names since they aren't in the DataSourceSummary */}
                      {["users", "events", "transactions", "sessions"].map(table => (
                        <span key={table} className="rounded bg-elevated px-2 py-1 font-mono text-[11px] text-ink border border-hairline">
                          {table}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </Shell>
  );
}
