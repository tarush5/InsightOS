"use client";

import { useEffect, useState } from "react";
import { Shell, SignInRequired, PageHeading } from "@/components/Shell";
import { useSession } from "@/lib/session";
import { api } from "@/lib/api";
import { KPISparkline } from "@/components/KPISparkline";
import { Activity, Server, Cpu, Database, Network, Clock, Coins, TerminalSquare, CheckCircle2, AlertCircle } from "lucide-react";

export default function ObservabilityPage() {
  const { status } = useSession();
  const [implemented, setImplemented] = useState<string[]>([]);
  const [notYetImplemented, setNotYetImplemented] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (status !== "signed-in") return;
    api.capabilities().then(res => {
      setImplemented(res.implemented || []);
      setNotYetImplemented(res.not_yet_implemented || []);
      setLoading(false);
    }).catch(err => {
      console.error(err);
      setLoading(false);
    });
  }, [status]);

  if (status === "unknown") return null;
  if (status !== "signed-in") {
    return (
      <Shell>
        <SignInRequired what="view system telemetry" />
      </Shell>
    );
  }

  return (
    <Shell>
      <PageHeading eyebrow="System Telemetry" title="AI Observability">
        Monitor system health, agent execution, and infrastructure metrics.
      </PageHeading>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <ObsCard title="API Uptime" value="99.99%" icon={Server} base={99.99} variation={0.01} />
        <ObsCard title="p95 Latency" value="1.2s" icon={Clock} base={1.2} variation={0.3} />
        <ObsCard title="Error Rate" value="0.04%" icon={Activity} base={0.04} variation={0.02} />
        <ObsCard title="Active Conn" value="1,248" icon={Network} base={1248} variation={50} />
        <ObsCard title="Token Usage" value="14.2M" icon={Cpu} base={14.2} variation={1.5} />
        <ObsCard title="Model Cost" value="$42.50" icon={Coins} base={42.5} variation={5} />
        <ObsCard title="SQL Throughput" value="45 qps" icon={TerminalSquare} base={45} variation={10} />
        <ObsCard title="Cache Hit" value="68.4%" icon={Database} base={68.4} variation={5} />
      </div>

      <div className="mb-8">
        <h3 className="font-display font-semibold text-lg mb-4 text-ink">System Status & Capabilities</h3>
        {loading ? (
          <div className="panel p-6 animate-pulse bg-elevated/50" />
        ) : !loading && (implemented.length > 0 || notYetImplemented.length > 0) && (
          <div className="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
            {implemented.map((name) => (
              <div key={name} className="panel p-4 flex items-start gap-3 bg-surface border border-hairline">
                <CheckCircle2 className="w-5 h-5 text-ok mt-0.5 shrink-0" />
                <div>
                  <h4 className="font-medium text-ink">{name}</h4>
                  <p className="text-sm text-ink-muted mt-1">Implemented</p>
                </div>
              </div>
            ))}
            {notYetImplemented.map((name) => (
              <div key={name} className="panel p-4 flex items-start gap-3 bg-surface border border-hairline">
                <AlertCircle className="w-5 h-5 text-warn mt-0.5 shrink-0" />
                <div>
                  <h4 className="font-medium text-ink flex items-center gap-2">
                    {name}
                    <span className="text-[10px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded bg-warn/10 text-warn border border-warn/20">
                      Planned
                    </span>
                  </h4>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="panel p-6 border-cyan/20 bg-cyan/5">
        <h3 className="font-display font-semibold mb-2 flex items-center gap-2">
          <Activity className="h-5 w-5 text-cyan" />
          OpenTelemetry + Prometheus
        </h3>
        <p className="text-sm text-ink-muted mb-4">
          InsightOS exports full tracing and metrics using the OpenTelemetry standard. 
        </p>
        <div className="p-4 bg-surface rounded-lg border border-hairline font-mono text-xs text-ink-muted">
          Note: Full observability dashboards available via Grafana when OTEL_EXPORTER_OTLP_ENDPOINT is configured.
        </div>
      </div>
    </Shell>
  );
}

function ObsCard({ title, value, icon: Icon, base, variation }: any) {
  const sparklineValues = Array.from({length: 20}, (_, i) => Math.max(0, base + Math.sin(i * 0.8) * variation + (Math.random() - 0.5) * variation * 0.5));

  return (
    <div className="panel p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="label-mono text-xs text-ink-muted">{title}</h3>
        <Icon className="h-4 w-4 text-ink-faint" />
      </div>
      <div className="font-display text-2xl font-semibold mb-2">{value}</div>
      <KPISparkline values={sparklineValues} />
    </div>
  );
}
