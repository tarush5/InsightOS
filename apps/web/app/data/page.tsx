"use client";

import { useEffect, useState } from "react";
import { Shell, SignInRequired, PageHeading, EmptyState } from "../../components/Shell";
import { useSession } from "../../lib/session";
import { api } from "../../lib/api";
import type { DataSourceSummary } from "../../lib/types";

export default function DataPage() {
  const { status } = useSession();

  if (status === "unknown") return <Shell><div className="animate-pulseSoft">Loading...</div></Shell>;
  if (status === "signed-out") return <Shell><SignInRequired what="manage data sources" /></Shell>;

  return <DataContent />;
}

function DataContent() {
  const [sources, setSources] = useState<DataSourceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [crawling, setCrawling] = useState<Record<string, boolean>>({});

  useEffect(() => {
    api.dataSources()
      .then((res) => {
        setSources(res.data_sources);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  const handleCrawl = async (id: string) => {
    setCrawling((prev) => ({ ...prev, [id]: true }));
    try {
      await api.crawlDataSource(id);
      // maybe refresh data sources here if we wanted to update last sync time
      const res = await api.dataSources();
      setSources(res.data_sources);
    } catch (err: any) {
      alert(`Crawl failed: ${err.message}`);
    } finally {
      setCrawling((prev) => ({ ...prev, [id]: false }));
    }
  };

  if (loading) {
    return (
      <Shell>
        <PageHeading eyebrow="Configuration" title="Data Source Management" />
        <div className="animate-pulseSoft text-ink-muted">Loading data sources...</div>
      </Shell>
    );
  }

  if (error) {
    return (
      <Shell>
        <PageHeading eyebrow="Configuration" title="Data Source Management" />
        <div className="text-crit">Error loading data sources: {error}</div>
      </Shell>
    );
  }

  return (
    <Shell>
      <PageHeading eyebrow="Configuration" title="Data Source Management">
        Connect and manage data warehouses, databases, and external APIs.
      </PageHeading>

      {sources.length === 0 ? (
        <EmptyState 
          title="No data sources connected yet." 
          body="Configure your warehouse credentials in .env to get started." 
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {sources.map((source) => {
            const healthColor =
              source.status === "healthy" ? "bg-ok" :
              source.status === "degraded" ? "bg-warn" :
              source.status === "error" ? "bg-crit" : "bg-ink-muted";

            return (
              <div key={source.id} className="panel flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="font-display font-semibold">{source.name}</h3>
                    <span className="rounded bg-elevated px-2 py-0.5 text-[10px] uppercase tracking-wider text-ink-muted border border-hairline">
                      {source.kind}
                    </span>
                  </div>
                  
                  <div className="grid gap-2 text-sm">
                    <div className="flex items-center gap-2">
                      <div className={`h-2 w-2 rounded-full ${healthColor}`} />
                      <span className="capitalize">{source.status}</span>
                      <span className="text-ink-faint ml-auto font-mono text-xs">Score: {source.health}</span>
                    </div>
                    <div className="text-ink-muted">
                      Last sync: {source.last_sync_at ? new Date(source.last_sync_at).toLocaleString() : "Never"}
                    </div>
                  </div>
                </div>

                <div className="mt-6">
                  <button
                    onClick={() => handleCrawl(source.id)}
                    disabled={crawling[source.id]}
                    className="w-full rounded-md bg-elevated py-2 text-xs font-medium text-ink transition-colors hover:bg-hairline disabled:opacity-50 border border-hairline"
                  >
                    {crawling[source.id] ? "Crawling..." : "Crawl Schema"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Shell>
  );
}
