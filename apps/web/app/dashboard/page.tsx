"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Shell, SignInRequired } from "@/components/Shell";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { HistoryRow, DemoScenario } from "@/lib/types";
import { KPISparkline } from "@/components/KPISparkline";
import {
  Search,
  Activity,
  Database,
  BellRing,
  LineChart,
  ArrowRight,
  Play,
  Clock,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Plus,
  BrainCircuit,
  Workflow,
  TrendingUp,
  TrendingDown
} from "lucide-react";

// Helper to format 'time ago'
function timeAgo(dateStr: string | null) {
  if (!dateStr) return "Unknown";
  const date = new Date(dateStr);
  const seconds = Math.floor((new Date().getTime() - date.getTime()) / 1000);
  let interval = seconds / 31536000;
  if (interval > 1) return Math.floor(interval) + "y ago";
  interval = seconds / 2592000;
  if (interval > 1) return Math.floor(interval) + "mo ago";
  interval = seconds / 86400;
  if (interval > 1) return Math.floor(interval) + "d ago";
  interval = seconds / 3600;
  if (interval > 1) return Math.floor(interval) + "h ago";
  interval = seconds / 60;
  if (interval > 1) return Math.floor(interval) + "m ago";
  return Math.floor(seconds) + "s ago";
}

function VerdictBadge({ verdict }: { verdict: string | null }) {
  if (!verdict) return <span className="text-muted text-xs">Unknown</span>;
  const v = verdict.toLowerCase();
  
  if (v === "ok" || v === "safe" || v === "healthy") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-ok/10 text-ok border border-ok/20">
        <CheckCircle2 className="w-3 h-3" />
        {verdict}
      </span>
    );
  }
  if (v === "warn" || v === "warning") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-warn/10 text-warn border border-warn/20">
        <AlertTriangle className="w-3 h-3" />
        {verdict}
      </span>
    );
  }
  if (v === "crit" || v === "critical" || v === "danger") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-crit/10 text-crit border border-crit/20">
        <XCircle className="w-3 h-3" />
        {verdict}
      </span>
    );
  }
  
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium bg-ink/10 text-ink border border-ink/20">
      {verdict}
    </span>
  );
}

export default function DashboardPage() {
  const { status, identity } = useSession();
  const router = useRouter();
  
  const [query, setQuery] = useState("");
  
  const [data, setData] = useState<{
    historyTotal: number;
    recentInvestigations: HistoryRow[];
    dataSourcesCount: number;
    activeAlertsCount: number;
    metricsCount: number;
    scenarios: DemoScenario[];
  } | null>(null);
  
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (status !== "signed-in") return;
    
    let mounted = true;
    setLoading(true);

    Promise.allSettled([
      api.history(5, 0),
      api.dataSources(),
      api.alerts(),
      api.metrics(),
      api.demoScenarios()
    ]).then(([histRes, dsRes, alRes, metRes, scRes]) => {
      if (!mounted) return;
      
      let historyTotal = 0;
      let recentInvestigations: HistoryRow[] = [];
      let dataSourcesCount = 0;
      let activeAlertsCount = 0;
      let metricsCount = 0;
      let scenarios: DemoScenario[] = [];
      
      if (histRes.status === "fulfilled" && histRes.value) {
        historyTotal = histRes.value.total || 0;
        recentInvestigations = histRes.value.investigations || [];
      } else {
        console.error("Failed to load history", (histRes as PromiseRejectedResult).reason);
      }
      
      if (dsRes.status === "fulfilled" && dsRes.value) {
        dataSourcesCount = dsRes.value.data_sources?.length || 0;
      }
      
      if (alRes.status === "fulfilled" && alRes.value) {
        activeAlertsCount = (alRes.value.alerts || []).filter((a: any) => a.is_active).length;
      }
      
      if (metRes.status === "fulfilled" && metRes.value) {
        metricsCount = metRes.value.metrics?.length || 0;
      }
      
      if (scRes.status === "fulfilled" && scRes.value) {
        scenarios = scRes.value.scenarios || [];
      }

      setData({
        historyTotal,
        recentInvestigations,
        dataSourcesCount,
        activeAlertsCount,
        metricsCount,
        scenarios
      });
      setLoading(false);
    });

    return () => {
      mounted = false;
    };
  }, [status]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    router.push(`/investigate?q=${encodeURIComponent(query.trim())}`);
  };

  if (status === "unknown") {
    return (
      <Shell>
        <div className="p-8 animate-pulse text-muted flex items-center justify-center min-h-[50vh]">
          Loading Command Center...
        </div>
      </Shell>
    );
  }

  if (status === "signed-out") {
    return (
      <Shell>
        <div className="p-8 max-w-2xl mx-auto mt-12">
          <SignInRequired what="access the Command Center" />
        </div>
      </Shell>
    );
  }

  const kpis = [
    { label: "Active Investigations", value: data?.historyTotal ?? 0, icon: Activity, trend: "Updated recently" },
    { label: "Connected Data", value: data?.dataSourcesCount ?? 0, icon: Database, trend: "Synced today" },
    { label: "Active Alerts", value: data?.activeAlertsCount ?? 0, icon: BellRing, trend: "Monitoring" },
    { label: "Available Metrics", value: data?.metricsCount ?? 0, icon: LineChart, trend: "Up to date" }
  ];

  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';

  return (
    <Shell>
      <div className="max-w-6xl mx-auto p-4 md:p-8 space-y-12 pb-24">
        {/* Hero Section */}
        <section className="space-y-6">
          <h1 className="text-4xl md:text-5xl font-display text-ink tracking-tight animate-riseIn">
            {greeting}.<br/>
            <span className="text-muted">What would you like to understand?</span>
          </h1>
          
          <form onSubmit={handleSearch} className="relative max-w-3xl animate-riseIn" style={{ animationDelay: '100ms' }}>
            <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none">
              <Search className="h-5 w-5 text-cyan" />
            </div>
            <input
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="e.g., Why did user retention drop last week in the EU segment?"
              className="w-full bg-surface border border-hairline rounded-xl py-4 pl-12 pr-24 text-lg text-ink placeholder:text-faint focus:outline-none focus:ring-2 focus:ring-cyan/50 focus:border-cyan transition-all shadow-sm"
            />
            <button
              type="submit"
              disabled={!query.trim()}
              className="absolute inset-y-2 right-2 bg-cyan text-surface font-medium px-4 rounded-lg hover:bg-cyan-dim disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              Investigate
            </button>
          </form>
        </section>

        {/* KPI Cards */}
        <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 animate-riseIn" style={{ animationDelay: '200ms' }}>
          {loading ? (
            Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="panel h-32 animate-pulseSoft bg-elevated rounded-xl border border-hairline" />
            ))
          ) : (
            kpis.map((kpi, i) => {
              const Icon = kpi.icon;
              const baseValue = kpi.value;
              const sparklineValues = Array.from({length: 14}, (_, i) => Math.max(0, baseValue + Math.sin(i * 0.5) * baseValue * 0.1 + (Math.random() - 0.5) * baseValue * 0.05));
              const isUp = sparklineValues.length > 1 && sparklineValues[sparklineValues.length - 1]! >= sparklineValues[0]!;
              
              return (
                <div key={i} className="panel flex flex-col justify-between p-5 rounded-xl bg-surface hover:bg-elevated transition-colors border border-hairline group">
                  <div className="flex justify-between items-start">
                    <span className="label-mono text-muted group-hover:text-ink transition-colors">{kpi.label}</span>
                    <Icon className="w-5 h-5 text-faint group-hover:text-cyan transition-colors" />
                  </div>
                  <div className="mt-4 flex flex-col">
                    <div className="flex items-baseline gap-2">
                      <span className="text-4xl font-display text-ink">{kpi.value}</span>
                      <span className="text-xs text-muted flex items-center gap-1">
                        {isUp ? <TrendingUp className="w-3 h-3 text-ok" /> : <TrendingDown className="w-3 h-3 text-crit" />}
                        {kpi.trend}
                      </span>
                    </div>
                    <div className="mt-2">
                      <KPISparkline values={sparklineValues} />
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </section>

        {/* Two-column layout */}
        <section className="grid grid-cols-1 lg:grid-cols-2 gap-8 animate-riseIn" style={{ animationDelay: '300ms' }}>
          
          {/* Recent Investigations */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-display text-ink flex items-center gap-2">
                <Clock className="w-5 h-5 text-violet" />
                Recent Investigations
              </h2>
              <Link href="/history" className="text-sm text-cyan hover:underline flex items-center gap-1">
                View all <ArrowRight className="w-4 h-4" />
              </Link>
            </div>
            
            <div className="panel p-0 overflow-hidden border border-hairline rounded-xl bg-surface">
              {loading ? (
                <div className="p-4 space-y-4">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="h-16 animate-pulseSoft bg-elevated rounded-lg" />
                  ))}
                </div>
              ) : data?.recentInvestigations.length === 0 ? (
                <div className="p-8 text-center space-y-3">
                  <div className="w-12 h-12 bg-elevated rounded-full flex items-center justify-center mx-auto mb-2 border border-hairline">
                    <Activity className="w-6 h-6 text-faint" />
                  </div>
                  <p className="text-ink font-medium">No recent investigations</p>
                  <p className="text-sm text-muted">Start an investigation by asking a question above.</p>
                </div>
              ) : (
                <div className="divide-y divide-hairline">
                  {data?.recentInvestigations.map(row => (
                    <Link
                      key={row.reference}
                      href={`/investigate?ref=${row.reference}`}
                      className="block p-4 hover:bg-elevated transition-colors group"
                    >
                      <div className="flex justify-between items-start gap-4">
                        <div className="space-y-1.5 overflow-hidden flex-1">
                          <p className="text-sm font-medium text-ink truncate group-hover:text-cyan transition-colors">
                            {row.question}
                          </p>
                          <div className="flex items-center gap-3 text-xs text-muted">
                            <span className="label-mono">{row.reference.slice(0,8)}</span>
                            <span>{timeAgo(row.created_at)}</span>
                            {row.confidence != null && (
                              <span className="text-violet-dim font-medium">{Math.round(row.confidence * 100)}% conf</span>
                            )}
                          </div>
                        </div>
                        <div className="flex-shrink-0 pt-0.5">
                          <VerdictBadge verdict={row.verdict} />
                        </div>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Demo Scenarios */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xl font-display text-ink flex items-center gap-2">
                <Play className="w-5 h-5 text-cyan" />
                Demo Scenarios
              </h2>
            </div>
            
            <div className="grid grid-cols-1 gap-3">
              {loading ? (
                Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="h-24 animate-pulseSoft bg-elevated rounded-xl border border-hairline" />
                ))
              ) : data?.scenarios.length === 0 ? (
                <div className="panel p-8 text-center rounded-xl border border-hairline bg-surface">
                  <p className="text-muted text-sm">No demo scenarios available.</p>
                </div>
              ) : (
                data?.scenarios.map(scenario => (
                  <div key={scenario.id} className="panel p-4 rounded-xl bg-surface border border-hairline flex items-center justify-between gap-4 hover:border-cyan/30 transition-colors group">
                    <div className="space-y-1.5 flex-1">
                      <p className="text-sm font-medium text-ink line-clamp-2 pr-2">
                        {scenario.question}
                      </p>
                      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                        <span className="px-1.5 py-0.5 bg-elevated rounded text-ink border border-hairline label-mono">
                          {scenario.metric_key}
                        </span>
                        {scenario.dimensions.length > 0 && (
                          <span className="text-faint">+ {scenario.dimensions.length} dims</span>
                        )}
                      </div>
                    </div>
                    <Link
                      href={`/investigate?scenario=${scenario.id}`}
                      className="flex-shrink-0 bg-elevated text-cyan border border-hairline hover:border-cyan/50 p-2 rounded-lg transition-all group-hover:bg-cyan/10"
                      title="Run scenario"
                    >
                      <Play className="w-4 h-4" />
                    </Link>
                  </div>
                ))
              )}
            </div>
          </div>
        </section>

        {/* Quick Actions */}
        <section className="space-y-4 animate-riseIn" style={{ animationDelay: '400ms' }}>
          <h2 className="text-xl font-display text-ink">Quick Actions</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Link href="/investigate" className="panel p-5 rounded-xl bg-surface border border-hairline hover:border-cyan/50 hover:bg-elevated transition-all group">
              <div className="w-10 h-10 rounded-lg bg-cyan/10 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform">
                <Plus className="w-5 h-5 text-cyan" />
              </div>
              <h3 className="font-medium text-ink">New Investigation</h3>
              <p className="text-xs text-muted mt-1">Start a causal analysis</p>
            </Link>
            
            <Link href="/data" className="panel p-5 rounded-xl bg-surface border border-hairline hover:border-ink/50 hover:bg-elevated transition-all group">
              <div className="w-10 h-10 rounded-lg bg-ink/10 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform">
                <Database className="w-5 h-5 text-ink" />
              </div>
              <h3 className="font-medium text-ink">Connect Data</h3>
              <p className="text-xs text-muted mt-1">Add new data sources</p>
            </Link>
            
            <Link href="/models" className="panel p-5 rounded-xl bg-surface border border-hairline hover:border-violet/50 hover:bg-elevated transition-all group">
              <div className="w-10 h-10 rounded-lg bg-violet/10 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform">
                <BrainCircuit className="w-5 h-5 text-violet" />
              </div>
              <h3 className="font-medium text-ink">Train Model</h3>
              <p className="text-xs text-muted mt-1">Fine-tune predictions</p>
            </Link>
            
            <Link href="/simulation" className="panel p-5 rounded-xl bg-surface border border-hairline hover:border-ok/50 hover:bg-elevated transition-all group">
              <div className="w-10 h-10 rounded-lg bg-ok/10 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform">
                <Workflow className="w-5 h-5 text-ok" />
              </div>
              <h3 className="font-medium text-ink">Run Simulation</h3>
              <p className="text-xs text-muted mt-1">Test hypothetical scenarios</p>
            </Link>
          </div>
        </section>
        
      </div>
    </Shell>
  );
}
