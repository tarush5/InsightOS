"use client";

import { useEffect, useState } from "react";
import { Shell, SignInRequired, PageHeading, EmptyState } from "../../components/Shell";
import { useSession } from "../../lib/session";
import { api } from "../../lib/api";
import type { MetricSummary } from "../../lib/types";

export default function SemanticLayerPage() {
  const { status } = useSession();

  if (status === "unknown") return <Shell><div className="animate-pulseSoft">Loading...</div></Shell>;
  if (status === "signed-out") return <Shell><SignInRequired what="view semantic layer" /></Shell>;

  return <SemanticContent />;
}

function SemanticContent() {
  const [metrics, setMetrics] = useState<MetricSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");

  useEffect(() => {
    api.metrics()
      .then((res) => {
        setMetrics(res.metrics);
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }, []);

  const filteredMetrics = metrics.filter(
    (m) => m.label.toLowerCase().includes(search.toLowerCase()) || m.key.toLowerCase().includes(search.toLowerCase())
  );

  if (loading) {
    return (
      <Shell>
        <PageHeading eyebrow="Governance" title="Business Semantic Layer" />
        <div className="animate-pulseSoft text-ink-muted">Loading metrics...</div>
      </Shell>
    );
  }

  return (
    <Shell>
      <PageHeading eyebrow="Governance" title="Business Semantic Layer">
        Governed metrics and dimensions used across investigations and dashboards.
      </PageHeading>

      <div className="mb-6">
        <input
          type="text"
          placeholder="Search metrics..."
          className="w-full max-w-md rounded-lg border border-hairline bg-surface px-4 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-cyan focus:outline-none focus:ring-1 focus:ring-cyan"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {metrics.length === 0 ? (
        <EmptyState 
          title="No semantic metrics defined." 
          body="Metrics are defined in your data catalog and synchronized here." 
        />
      ) : filteredMetrics.length === 0 ? (
        <EmptyState 
          title="No matching metrics." 
          body="Try a different search term." 
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {filteredMetrics.map((metric) => (
            <div key={metric.key} className="panel flex flex-col">
              <div className="flex items-start justify-between mb-2">
                <div>
                  <h3 className="font-display font-semibold">{metric.label}</h3>
                  <p className="font-mono text-[10px] text-ink-faint mt-1">{metric.key}</p>
                </div>
                <div className="flex gap-2">
                  <span className="rounded bg-elevated px-2 py-0.5 text-[10px] uppercase tracking-wider text-ink-muted border border-hairline">
                    {metric.unit}
                  </span>
                  <span className={`rounded px-2 py-0.5 text-[10px] uppercase tracking-wider ${metric.status === 'active' ? 'bg-ok/20 text-ok' : 'bg-warn/20 text-warn'}`}>
                    {metric.status}
                  </span>
                </div>
              </div>
              
              <p className="text-sm text-ink-muted mb-4 flex-1">
                {metric.description}
              </p>
              
              <div className="mb-4 rounded bg-surface p-3 font-mono text-xs text-ink-muted border border-hairline overflow-x-auto whitespace-pre">
                {`SELECT ${metric.key} FROM ...`} {/* Placeholder for formula display */}
              </div>

              <div>
                <p className="text-[10px] uppercase tracking-wider text-ink-faint mb-2">Dimensions</p>
                <div className="flex flex-wrap gap-1">
                  {metric.dimensions.map((dim) => (
                    <span key={dim} className="rounded bg-elevated px-2 py-1 font-mono text-[10px] text-ink">
                      {dim}
                    </span>
                  ))}
                  {metric.dimensions.length === 0 && (
                    <span className="text-xs text-ink-muted italic">None</span>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Shell>
  );
}
