"use client";

import { useEffect, useState } from "react";
import { Shell, SignInRequired, PageHeading, EmptyState } from "../../components/Shell";
import { useSession } from "../../lib/session";
import { api } from "../../lib/api";
import type { DataSourceSummary } from "../../lib/types";
import type { TrainResult } from "../../lib/api";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";

export default function ModelsPage() {
  const { status } = useSession();

  if (status === "unknown") return <Shell><div className="animate-pulseSoft">Loading...</div></Shell>;
  if (status === "signed-out") return <Shell><SignInRequired what="access ML Lab" /></Shell>;

  return <ModelsContent />;
}

function ModelsContent() {
  const [sources, setSources] = useState<DataSourceSummary[]>([]);
  const [loading, setLoading] = useState(true);
  
  // Form State
  const [sourceId, setSourceId] = useState("");
  const [tableName, setTableName] = useState("");
  const [targetColumn, setTargetColumn] = useState("");
  const [excludeColumns, setExcludeColumns] = useState("");
  const [dateColumn, setDateColumn] = useState("");
  
  const [training, setTraining] = useState(false);
  const [result, setResult] = useState<TrainResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.dataSources()
      .then((res) => {
        setSources(res.data_sources ?? []);
        if (res.data_sources && res.data_sources.length > 0) {
          setSourceId(res.data_sources[0]!.id);
        }
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTraining(true);
    setError(null);
    setResult(null);

    try {
      const res = await api.trainModel({
        data_source_id: sourceId,
        table: tableName,
        target: targetColumn,
        date_column: dateColumn ? dateColumn : undefined,
        exclude: excludeColumns.split(",").map(s => s.trim()).filter(Boolean),
      });
      
      setResult(res);
    } catch (err: any) {
      setError(err.message || "An error occurred during training.");
    } finally {
      setTraining(false);
    }
  };

  return (
    <Shell>
      <PageHeading eyebrow="Machine Learning" title="ML Model Lab">
        AutoML Training & Evaluation for predictive insights.
      </PageHeading>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-6">
          <form onSubmit={handleSubmit} className="panel space-y-4 p-6">
            <h2 className="font-display font-semibold mb-4">Training Configuration</h2>
            
            {loading ? (
              <div className="text-sm text-ink-muted">Loading data sources...</div>
            ) : (
              <>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-ink-muted">Data Source</label>
                  <select 
                    value={sourceId}
                    onChange={(e) => setSourceId(e.target.value)}
                    className="w-full rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-ink focus:border-cyan focus:outline-none"
                    required
                  >
                    <option value="" disabled>Select a data source...</option>
                    {sources.map(s => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                </div>
                
                <div className="space-y-1">
                  <label className="text-xs font-medium text-ink-muted">Table Name</label>
                  <input 
                    type="text" 
                    value={tableName}
                    onChange={(e) => setTableName(e.target.value)}
                    placeholder="e.g. customer_churn"
                    className="w-full rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-ink focus:border-cyan focus:outline-none"
                    required
                  />
                </div>
                
                <div className="space-y-1">
                  <label className="text-xs font-medium text-ink-muted">Target Column</label>
                  <input 
                    type="text" 
                    value={targetColumn}
                    onChange={(e) => setTargetColumn(e.target.value)}
                    placeholder="e.g. has_churned"
                    className="w-full rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-ink focus:border-cyan focus:outline-none"
                    required
                  />
                </div>
                
                <div className="space-y-1">
                  <label className="text-xs font-medium text-ink-muted">Date Column (Optional)</label>
                  <input 
                    type="text" 
                    value={dateColumn}
                    onChange={(e) => setDateColumn(e.target.value)}
                    placeholder="e.g. created_at"
                    className="w-full rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-ink focus:border-cyan focus:outline-none"
                  />
                </div>
                
                <div className="space-y-1">
                  <label className="text-xs font-medium text-ink-muted">Exclude Columns (comma-separated)</label>
                  <input 
                    type="text" 
                    value={excludeColumns}
                    onChange={(e) => setExcludeColumns(e.target.value)}
                    placeholder="e.g. id, email"
                    className="w-full rounded-md border border-hairline bg-surface px-3 py-2 text-sm text-ink focus:border-cyan focus:outline-none"
                  />
                </div>
                
                <button
                  type="submit"
                  disabled={training}
                  className="w-full mt-4 rounded-md bg-cyan px-4 py-2 text-sm font-medium text-black transition-opacity hover:opacity-90 disabled:opacity-50"
                >
                  {training ? "Training Model..." : "Train Model"}
                </button>
                
                {error && <div className="text-crit text-sm mt-2">{error}</div>}
              </>
            )}
          </form>

          {result ? (
            <div className="panel animate-riseIn p-6">
              <h3 className="font-display font-semibold mb-4 text-violet">Training Results</h3>
              <div className="grid gap-4 md:grid-cols-4 mb-6">
                <div className="rounded bg-surface p-3 border border-hairline">
                  <p className="text-[10px] text-ink-muted uppercase">Model Type</p>
                  <p className="font-mono text-sm mt-1">{result.model_type}</p>
                </div>
                <div className="rounded bg-surface p-3 border border-hairline">
                  <p className="text-[10px] text-ink-muted uppercase">Duration</p>
                  <p className="font-mono text-sm mt-1">{(result.duration_ms / 1000).toFixed(1)}s</p>
                </div>
                <div className="rounded bg-surface p-3 border border-hairline">
                  <p className="text-[10px] text-ink-muted uppercase">Task</p>
                  <p className="font-mono text-sm mt-1">{result.task}</p>
                </div>
                <div className="rounded bg-surface p-3 border border-hairline">
                  <p className="text-[10px] text-ink-muted uppercase">Run ID</p>
                  <p className="font-mono text-xs mt-1 truncate" title={result.run_id}>{result.run_id}</p>
                </div>
              </div>
              
              <div className="mb-6">
                <h4 className="text-xs font-medium mb-3 uppercase tracking-widest text-ink-muted">Metrics</h4>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                  {Object.entries(result.metrics || {}).map(([k, v]) => (
                    <div key={k} className="p-2 border border-hairline rounded bg-surface">
                      <p className="text-[10px] text-ink-muted uppercase">{k}</p>
                      <p className="font-mono text-sm mt-1">{typeof v === 'number' ? v.toFixed(4) : String(v)}</p>
                    </div>
                  ))}
                </div>
              </div>
              
              <div className="mb-6">
                <h4 className="text-xs font-medium mb-3 uppercase tracking-widest text-ink-muted">Feature Importances</h4>
                <div className="h-64 w-full">
                  {result.feature_importances && result.feature_importances.length > 0 ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={result.feature_importances} layout="vertical" margin={{ top: 0, right: 20, left: 60, bottom: 0 }}>
                        <XAxis type="number" stroke="#5D6474" tick={{ fontSize: 10 }} />
                        <YAxis dataKey="feature" type="category" stroke="#5D6474" tick={{ fontSize: 10 }} />
                        <Tooltip contentStyle={{ backgroundColor: "#101218", borderColor: "#22252F" }} />
                        <Bar dataKey="importance" fill="#8B5CF6" radius={[0, 4, 4, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  ) : (
                    <p className="text-sm text-ink-muted">No feature importances returned.</p>
                  )}
                </div>
              </div>

              {result.warnings && result.warnings.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium mb-2 uppercase tracking-widest text-warn">Warnings</h4>
                  <ul className="list-disc pl-5 text-sm text-warn/80 space-y-1">
                    {result.warnings.map((w: string, i: number) => <li key={i}>{w}</li>)}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            !training && <div className="h-48 flex items-center justify-center panel"><EmptyState title="No model trained yet" body="Configure the parameters above and start training to see evaluation metrics." /></div>
          )}
        </div>
        
        <div className="panel h-fit border-cyan-dim/30 bg-surface/50 p-6">
          <h3 className="font-display font-semibold mb-3">AutoML Capabilities</h3>
          <p className="text-sm text-ink-muted mb-4">
            InsightOS automatically handles the end-to-end model training lifecycle:
          </p>
          <ul className="space-y-3 text-sm text-ink-muted">
            <li className="flex gap-2"><span className="text-cyan">•</span> Data preprocessing & imputation</li>
            <li className="flex gap-2"><span className="text-cyan">•</span> Feature engineering</li>
            <li className="flex gap-2"><span className="text-cyan">•</span> Algorithm selection (XGBoost, Random Forest, etc.)</li>
            <li className="flex gap-2"><span className="text-cyan">•</span> Hyperparameter tuning</li>
            <li className="flex gap-2"><span className="text-cyan">•</span> Cross-validation</li>
          </ul>
        </div>
      </div>
    </Shell>
  );
}
