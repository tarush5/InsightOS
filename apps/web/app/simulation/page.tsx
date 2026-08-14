"use client";

import { useState, useEffect } from "react";
import { Shell, SignInRequired, PageHeading, EmptyState } from "@/components/Shell";
import { useSession } from "@/lib/session";
import { api } from "@/lib/api";
import { MetricSummary } from "@/lib/types";
import { Play, Plus, Trash2 } from "lucide-react";
import { SimulationChart } from "@/components/SimulationChart";

export default function SimulationPage() {
  const { status } = useSession();

  const [metrics, setMetrics] = useState<MetricSummary[]>([]);
  
  const [metricKey, setMetricKey] = useState("revenue");
  const [dimension, setDimension] = useState("region");
  const [baselineStart, setBaselineStart] = useState("2024-01-01");
  const [baselineEnd, setBaselineEnd] = useState("2024-06-01");
  const [horizonDays, setHorizonDays] = useState(90);
  
  const [levers, setLevers] = useState([{ segment: "South", change_pct: 10, ramp_days: 14, rationale: "" }]);

  const [isSimulating, setIsSimulating] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "signed-in") {
      api.metrics().then((res) => setMetrics(res.metrics)).catch(() => {});
    }
  }, [status]);

  if (status === "unknown") return null;
  if (status !== "signed-in") {
    return (
      <Shell>
        <SignInRequired what="run simulations" />
      </Shell>
    );
  }

  const handleRun = async () => {
    setIsSimulating(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.simulate({
        metric_key: metricKey,
        dimension,
        baseline_start: baselineStart,
        baseline_end: baselineEnd,
        levers: levers.map(l => ({ ...l, change_pct: Number(l.change_pct), ramp_days: Number(l.ramp_days) })),
        horizon_days: Number(horizonDays)
      });
      setResult(res);
    } catch (err: any) {
      console.error(err);
      setError(err.message || "An error occurred during simulation.");
    } finally {
      setIsSimulating(false);
    }
  };

  const addLever = () => {
    setLevers([...levers, { segment: "", change_pct: 0, ramp_days: 0, rationale: "" }]);
  };

  const removeLever = (index: number) => {
    setLevers(levers.filter((_, i) => i !== index));
  };

  const updateLever = (index: number, key: string, value: any) => {
    const newLevers = [...levers];
    newLevers[index] = { ...newLevers[index]!, [key]: value };
    setLevers(newLevers);
  };

  return (
    <Shell>
      <PageHeading eyebrow="What-If Analysis" title="Scenario Simulator">
        Adjust business levers below to simulate projected impact on core metrics.
      </PageHeading>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-1 space-y-6 panel p-6">
          <h2 className="font-display font-semibold text-lg">Simulation Config</h2>
          
          <div className="space-y-4">
            <div className="space-y-1">
              <label className="label-mono text-xs text-ink-muted">Metric Key</label>
              <select className="grid-field w-full" value={metricKey} onChange={e => setMetricKey(e.target.value)}>
                {metrics.length === 0 && <option value="revenue">revenue</option>}
                {metrics.map(m => (
                  <option key={m.key} value={m.key}>{m.label}</option>
                ))}
              </select>
            </div>
            
            <div className="space-y-1">
              <label className="label-mono text-xs text-ink-muted">Dimension</label>
              <select className="grid-field w-full" value={dimension} onChange={e => setDimension(e.target.value)}>
                <option value="region">region</option>
                <option value="segment">segment</option>
                <option value="channel">channel</option>
                <option value="category">category</option>
              </select>
            </div>
            
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1">
                <label className="label-mono text-xs text-ink-muted">Baseline Start</label>
                <input type="date" className="grid-field w-full" value={baselineStart} onChange={e => setBaselineStart(e.target.value)} />
              </div>
              <div className="space-y-1">
                <label className="label-mono text-xs text-ink-muted">Baseline End</label>
                <input type="date" className="grid-field w-full" value={baselineEnd} onChange={e => setBaselineEnd(e.target.value)} />
              </div>
            </div>
            
            <div className="space-y-1">
              <label className="label-mono text-xs text-ink-muted flex justify-between">
                <span>Horizon Days</span>
                <span className="text-cyan">{horizonDays}</span>
              </label>
              <input type="range" min="7" max="365" step="1" className="w-full accent-cyan" value={horizonDays} onChange={e => setHorizonDays(Number(e.target.value))} />
            </div>

            <div className="pt-4 border-t border-hairline space-y-4">
              <div className="flex items-center justify-between">
                <h3 className="label-mono text-xs text-ink-muted">Levers</h3>
                <button onClick={addLever} className="text-cyan text-xs flex items-center gap-1 hover:underline">
                  <Plus className="h-3 w-3" /> Add
                </button>
              </div>
              
              {levers.map((lever, i) => (
                <div key={i} className="space-y-3 p-3 bg-surface border border-hairline rounded-lg">
                  <div className="flex justify-between items-center gap-2">
                    <input type="text" placeholder="Segment (e.g. South)" className="grid-field text-xs flex-1" value={lever.segment} onChange={e => updateLever(i, 'segment', e.target.value)} />
                    <button onClick={() => removeLever(i)} className="text-crit p-1 hover:bg-crit/10 rounded"><Trash2 className="h-3 w-3" /></button>
                  </div>
                  <div className="space-y-1">
                    <label className="text-[10px] text-ink-muted flex justify-between">
                      <span>Change %</span>
                      <span className="text-cyan">{lever.change_pct}%</span>
                    </label>
                    <input type="range" min="-100" max="1000" step="1" className="w-full accent-cyan" value={lever.change_pct} onChange={e => updateLever(i, 'change_pct', e.target.value)} />
                  </div>
                  <div className="space-y-1">
                    <label className="text-[10px] text-ink-muted">Ramp Days</label>
                    <input type="number" min="0" className="grid-field text-xs w-full" value={lever.ramp_days} onChange={e => updateLever(i, 'ramp_days', e.target.value)} />
                  </div>
                  <div className="space-y-1">
                    <label className="text-[10px] text-ink-muted">Rationale (optional)</label>
                    <input type="text" placeholder="Why this change?" className="grid-field text-xs w-full" value={lever.rationale} onChange={e => updateLever(i, 'rationale', e.target.value)} />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <button
            onClick={handleRun}
            disabled={isSimulating}
            className="w-full mt-6 flex items-center justify-center gap-2 rounded-lg bg-cyan text-base font-medium px-4 py-3 transition-opacity hover:opacity-90 disabled:opacity-50 text-black"
          >
            {isSimulating ? "Simulating..." : <><Play className="h-4 w-4" /> Run Simulation</>}
          </button>
          
          {error && <div className="text-crit text-sm mt-2">{error}</div>}
        </div>

        <div className="lg:col-span-2">
          {!result ? (
            <div className="h-full flex items-center justify-center min-h-[300px]">
              <EmptyState title="Ready to Simulate" body="Configure your scenario and click Run Simulation" />
            </div>
          ) : (
            <div className="space-y-6 animate-riseIn">
              <div className="grid grid-cols-3 gap-4">
                <div className="panel p-4">
                  <p className="text-[10px] text-ink-muted uppercase tracking-widest">Metric</p>
                  <p className="font-mono mt-1">{result.metric_key}</p>
                </div>
                <div className="panel p-4">
                  <p className="text-[10px] text-ink-muted uppercase tracking-widest">Dimension</p>
                  <p className="font-mono mt-1">{result.dimension}</p>
                </div>
                <div className="panel p-4">
                  <p className="text-[10px] text-ink-muted uppercase tracking-widest">Baseline Window</p>
                  <p className="font-mono mt-1 text-sm">{result.baseline_window?.[0]} to {result.baseline_window?.[1]}</p>
                </div>
              </div>
              
              <div className="panel p-6">
                <h3 className="label-mono text-xs text-ink-muted mb-4">Summary Metrics</h3>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  {Object.entries(result.summary || {}).map(([k, v]) => (
                    <div key={k}>
                      <p className="text-[10px] text-ink-muted uppercase tracking-widest">{k.replace(/_/g, ' ')}</p>
                      <p className="font-mono mt-1 text-lg">
                        {typeof v === 'number' && v > 1000 ? new Intl.NumberFormat('en', { notation: 'compact' }).format(v) : String(v)}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
              
              <div className="panel p-6 h-[400px]">
                <SimulationChart segments={Array.isArray(result.result) ? (result.result as any[]).map((r: any) => ({ segment: String(r.segment ?? ''), baseline: Number(r.baseline ?? 0), simulated: Number(r.simulated ?? 0), change_pct: Number(r.change_pct ?? 0) })) : []} />
              </div>
            </div>
          )}
        </div>
      </div>
    </Shell>
  );
}
