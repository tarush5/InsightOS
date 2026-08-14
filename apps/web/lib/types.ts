export type StageState = "pending" | "running" | "done" | "skipped" | "failed";

export interface InvestigationEvent {
  investigation_id: string;
  stage: string;
  state: StageState;
  label: string;
  progress: number;
  elapsed_ms: number;
  detail: Record<string, unknown>;
}

export interface Driver {
  dimension: string;
  segment: string;
  prev_value: number;
  curr_value: number;
  absolute_change: number;
  segment_pct_change: number | null;
  contribution_pct: number;
  share_of_change: number;
  status: "changed" | "new" | "lost";
}

export interface Confidence {
  data: number;
  statistical: number;
  model: number;
  reasoning: number;
  overall: number;
  label: "high" | "moderate" | "low" | "insufficient";
  limiting_factor: string;
  rationale: string[];
}

export interface CriticCheck {
  name: string;
  passed: boolean;
  blocking: boolean;
  detail: string;
}

export interface Recommendation {
  problem: string;
  evidence: string;
  recommendation: string;
  assumptions: string[];
  expected_impact_pct: number;
  expected_impact_absolute: number;
  confidence: string;
  risk: string;
  priority: number;
}

export interface Forecast {
  model: string;
  horizon: number;
  dates: string[];
  predicted: number[];
  lower_80: number[];
  upper_80: number[];
  history_dates: string[];
  history_values: number[];
  trend_direction: string;
  seasonality_detected: boolean;
  caveats: string[];
  metrics: { mae: number; rmse: number; mape: number | null; mase: number; beats_baseline: boolean };
}

export interface InvestigationResult {
  verdict: "answered" | "insufficient_evidence";
  headline: string;
  narrative?: string;
  drivers?: Driver[];
  recommendations?: Recommendation[];
  confidence?: Confidence;
  critic?: { approved: boolean; passed: number; total: number; blocking_failures: number; checks: CriticCheck[] };
  forecast?: Forecast | null;
  evidence?: Record<string, unknown>;
  usage?: { call_count: number; total_tokens: number; total_cost_usd: number; degraded: boolean };
}

export interface DemoScenario {
  id: string;
  question: string;
  metric_key: string;
  current_start: string;
  current_end: string;
  comparison_start: string;
  comparison_end: string;
  dimensions: string[];
}

/* --- Auth ------------------------------------------------------------------ */

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  workspace_id: string;
  role: string;
}

export interface Identity {
  user_id: string;
  workspace_id: string;
  role: string;
  permissions: string[];
}

/* --- History --------------------------------------------------------------- */

export interface HistoryRow {
  reference: string;
  question: string;
  metric_key: string | null;
  status: string;
  verdict: string | null;
  headline: string | null;
  confidence: number | null;
  duration_ms: number | null;
  created_at: string | null;
}

/* --- Alerts ---------------------------------------------------------------- */

export type AlertCondition = "threshold" | "change" | "anomaly";

export interface AlertRule {
  metric_key: string;
  condition: AlertCondition;
  operator: "lt" | "gt";
  threshold: number;
  window_days: number;
  comparison_days: number;
  min_severity: string;
  segment: Record<string, string>;
  cooldown_hours: number;
  source_text: string;
}

export interface AlertBacktest {
  available: boolean;
  reason?: string;
  anchored_on?: string;
  warehouse_is_stale?: boolean;
  history_days?: number;
  would_have_fired?: number;
  estimated_per_month?: number;
  noisy?: boolean;
  first_fires?: { date: string; reason: string; severity: string }[];
}

export interface AlertPreview {
  rule: AlertRule;
  readback: string;
  backtest: AlertBacktest;
}

export interface AlertSummary {
  id: string;
  name: string;
  metric_key: string;
  is_active: boolean;
  rule: AlertRule;
  natural_language: string;
  last_triggered_at: string | null;
}

/* --- Causal ---------------------------------------------------------------- */

export interface Diagnostic {
  name: string;
  passed: boolean;
  statistic: number;
  p_value: number;
  detail: string;
}

export interface CausalEstimate {
  att: number;
  std_error: number;
  p_value: number;
  ci_95: [number, number];
  relative_att: number | null;
  significant: boolean;
  credible: boolean;
  method: string;
  sample: {
    treated_units: number;
    control_units: number;
    observations: number;
    clusters: number;
    degrees_of_freedom: number;
  };
  diagnostics: Diagnostic[];
  caveats: string[];
  interpretation: string;
}

export interface PanelInfo {
  grain: string;
  periods: number;
  dropped_periods: string[];
  note: string | null;
}

export interface CausalResponse {
  metric_key: string;
  dimension: string;
  treated_units: string[];
  treatment_date: string;
  panel: PanelInfo;
  estimate: CausalEstimate;
}

export interface MetricSummary {
  key: string;
  label: string;
  description: string;
  unit: string;
  status: string;
  dimensions: string[];
}

/* --- Data sources and ad-hoc query ----------------------------------------- */

export interface DataSourceSummary {
  id: string;
  name: string;
  kind: string;
  secret_ref: string;
  status: string;
  health: string;
  last_sync_at: string | null;
}

export interface AskResult {
  question?: string;
  sql: string;
  attempts: number | { sql: string; ok: boolean; errors: string[] }[];
  columns?: string[];
  rows?: Record<string, unknown>[];
  row_count?: number;
  truncated?: boolean;
  duration_ms?: number;
  warnings?: string[];
  executed?: boolean;
  referenced_tables?: string[];
  /** Ad-hoc SQL is not equivalent to a governed metric; this says so. */
  provenance: string;
}

/* --- Documents ------------------------------------------------------------- */

export interface DocumentRecord {
  document_id: string;
  title: string;
  chunk_count: number;
  char_count: number;
  ingested_at: string;
  /** Chunks containing instruction-shaped text addressed to an AI system. */
  flagged_chunks: number;
}

export interface Citation {
  document_id: string;
  chunk_id: string;
  ordinal: number;
  heading_path: string[];
  chars: [number, number];
  text: string;
  rank: number;
  matched_by: string[];
}

export interface DocumentSearchResult {
  query: string;
  results: Citation[];
  excluded: { chunk_id: string; action: string; labels: string[] }[];
  retrieval: { chunks: number; degraded: boolean; note: string; signals: string[] };
  note: string;
}
