import type {
  AlertPreview,
  AlertSummary,
  AskResult,
  CausalResponse,
  DataSourceSummary,
  DemoScenario,
  DocumentRecord,
  DocumentSearchResult,
  HistoryRow,
  InvestigationEvent,
  MetricSummary,
} from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly suggestedFix?: string,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/* The session module installs this. Keeping the client ignorant of *how* the
   token is stored means the storage decision can change (to httpOnly cookies)
   without touching every call site. */
let authProvider: () => string | null = () => null;

export function setAuthProvider(provider: () => string | null) {
  authProvider = provider;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = authProvider();
  const res = await fetch(`${API_BASE}/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail ?? body.message;
    const message =
      typeof detail === "string"
        ? detail
        : (detail?.message ?? body.message ?? `Request failed (${res.status})`);
    throw new ApiError(message, res.status, body.suggested_fix, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

/* -------------------------------------------------------------------------- */
/*  Investigation types (inline to keep the API module self-contained)         */
/* -------------------------------------------------------------------------- */

export interface InvestigationPayload {
  question: string;
  metric_key?: string | null;
  current_start?: string | null;
  current_end?: string | null;
  comparison_start?: string | null;
  comparison_end?: string | null;
  dimensions?: string[];
  forecast_horizon?: number;
}

export interface InvestigationFullResult {
  reference: string;
  timeline: Record<string, unknown>[];
  result: Record<string, unknown> | null;
}

/* -------------------------------------------------------------------------- */
/*  Simulation types (match backend SimulationRequest exactly)                 */
/* -------------------------------------------------------------------------- */

export interface LeverSpec {
  segment: string;
  change_pct: number;
  ramp_days?: number;
  basis?: string;
  rationale?: string;
}

export interface SimulationPayload {
  metric_key: string;
  dimension?: string;
  baseline_start: string;
  baseline_end: string;
  levers: LeverSpec[];
  horizon_days?: number;
}

export interface SimulationResult {
  metric_key: string;
  dimension: string;
  baseline_window: string[];
  summary: Record<string, unknown>;
  result: Record<string, unknown>;
}

/* -------------------------------------------------------------------------- */
/*  Causal types (match backend CausalRequest exactly)                        */
/* -------------------------------------------------------------------------- */

export interface CausalPayload {
  metric_key: string;
  dimension?: string;
  treated_units: string[];
  control_units?: string[] | null;
  treatment_date: string;
  start: string;
  end: string;
  grain?: string;
}

/* -------------------------------------------------------------------------- */
/*  Model training types (match backend TrainRequest exactly)                  */
/* -------------------------------------------------------------------------- */

export interface TrainPayload {
  data_source_id: string;
  table: string;
  target: string;
  date_column?: string | null;
  exclude?: string[];
  max_rows?: number;
}

export interface TrainResult {
  source_rows: number;
  run_id: string;
  task: string;
  model_type: string;
  metrics: Record<string, number>;
  feature_importances: { feature: string; importance: number }[];
  training_rows: number;
  holdout_rows: number;
  duration_ms: number;
  columns_used: string[];
  warnings: string[];
}

/* -------------------------------------------------------------------------- */
/*  Data source schema types                                                  */
/* -------------------------------------------------------------------------- */

export interface DataSourceSchema {
  datasets: {
    name: string;
    physical_table: string;
    approx_rows: number;
    pii_columns: string[];
    columns: { name: string; dtype: string; nullable: boolean; sample_values?: string[] }[];
  }[];
}

/* -------------------------------------------------------------------------- */
/*  API Client                                                                */
/* -------------------------------------------------------------------------- */

export const api = {
  capabilities: () =>
    request<{
      llm_enabled: boolean;
      degraded_mode: boolean;
      degraded_note: string | null;
      implemented: string[];
      not_yet_implemented: string[];
    }>("/capabilities"),

  demoScenarios: () =>
    request<{ scenarios: DemoScenario[] }>("/investigations/demo/scenarios"),

  metrics: () => request<{ metrics: MetricSummary[] }>("/metrics"),

  metricSearch: (q: string) =>
    request<{ query: string; matches: MetricSummary[] }>(`/metrics/search?q=${encodeURIComponent(q)}`),

  metricDetail: (key: string) =>
    request<MetricSummary>(`/metrics/${encodeURIComponent(key)}`),

  history: (limit = 25, offset = 0) =>
    request<{ total: number; investigations: HistoryRow[] }>(
      `/investigations/history?limit=${limit}&offset=${offset}`,
    ),

  investigation: (reference: string) =>
    request<Record<string, unknown>>(`/investigations/${reference}`),

  /** Export an investigation as markdown or PDF */
  investigationExport: (reference: string, format: "markdown" | "pdf" = "markdown") =>
    request<string>(`/investigations/${reference}/export?format=${format}`),

  /** Run a synchronous investigation (HTTP POST fallback) */
  investigate: (payload: InvestigationPayload) =>
    request<InvestigationFullResult>("/investigations", json(payload)),

  previewAlert: (text: string, backtestDays = 180) =>
    request<AlertPreview>("/alerts/preview", json({ text, backtest_days: backtestDays })),

  createAlert: (name: string, text: string) =>
    request<{ id: string; readback: string }>("/alerts", json({ name, text })),

  alerts: () => request<{ alerts: AlertSummary[] }>("/alerts"),

  setAlertActive: (id: string, isActive: boolean) =>
    request<{ id: string; is_active: boolean }>(`/alerts/${id}?is_active=${isActive}`, {
      method: "PATCH",
    }),

  evaluateAlert: (id: string) =>
    request<Record<string, unknown>>(`/alerts/${id}/evaluate`, { method: "POST" }),

  /** Causal diff-in-diff analysis */
  diffInDiff: (payload: CausalPayload) =>
    request<CausalResponse>("/analysis/causal/diff-in-diff", json(payload)),

  /** Scenario simulation with segment-level levers */
  simulate: (payload: SimulationPayload) =>
    request<SimulationResult>("/analysis/simulate", json(payload)),

  /** Break-even analysis */
  breakEven: (payload: {
    metric_key: string;
    dimension?: string;
    baseline_start: string;
    baseline_end: string;
    segment: string;
    target_delta: number;
    horizon_days?: number;
  }) => request<Record<string, unknown>>("/analysis/simulate/break-even", json(payload)),

  dataSources: () =>
    request<{ data_sources: DataSourceSummary[] }>("/datasources"),

  dataSourceSchema: (id: string) =>
    request<DataSourceSchema>(`/datasources/${id}/schema`),

  crawlDataSource: (id: string) =>
    request<Record<string, unknown>>(`/datasources/${id}/crawl`, { method: "POST" }),

  testDataSource: (id: string) =>
    request<{ data_source_id: string; healthy: boolean; dialect: string; tables_in_catalog: number }>(
      `/datasources/${id}/test`, { method: "POST" }),

  /** Generates and validates SQL without running it. */
  explainQuery: (question: string, dataSourceId: string) =>
    request<AskResult>("/query/explain", json({ question, data_source_id: dataSourceId })),

  askQuery: (question: string, dataSourceId: string) =>
    request<AskResult>("/query/ask", json({ question, data_source_id: dataSourceId })),

  /** Train an AutoML model */
  trainModel: (payload: TrainPayload) =>
    request<TrainResult>("/models/train", json(payload)),

  documents: () => request<{ documents: DocumentRecord[] }>("/documents"),

  ingestDocument: (title: string, text: string) =>
    request<DocumentRecord & { warning?: string }>("/documents", json({ title, text })),

  searchDocuments: (query: string, topK = 5) =>
    request<DocumentSearchResult>("/documents/search", json({ query, top_k: topK })),

  /** Ask a question against ingested documents */
  askDocuments: (query: string, topK = 5) =>
    request<Record<string, unknown>>("/documents/ask", json({ query, top_k: topK })),

  socketTicket: () =>
    request<{ ticket: string; expires_in: number }>("/investigations/ticket", {
      method: "POST",
    }),
};

/**
 * Opens the investigation WebSocket and yields each stage transition.
 *
 * The socket is authenticated with a 60-second ticket rather than the access
 * token, because a WebSocket URL ends up in proxy logs and browser history.
 * If the socket cannot be opened the caller falls back to the synchronous POST,
 * so a proxy that blocks WS degrades to a slower experience rather than none.
 */
export async function streamInvestigation(
  payload: Record<string, unknown>,
  handlers: {
    onEvent: (event: InvestigationEvent) => void;
    onError: (message: string) => void;
    onClose: () => void;
  },
): Promise<() => void> {
  let ticket: string;
  try {
    ticket = (await api.socketTicket()).ticket;
  } catch {
    handlers.onError("Could not authenticate the live connection. Sign in and try again.");
    handlers.onClose();
    return () => {};
  }

  const url = `${API_BASE.replace(/^http/, "ws")}/api/v1/investigations/stream?ticket=${encodeURIComponent(ticket)}`;
  let socket: WebSocket | null = null;
  let closedByUs = false;

  try {
    socket = new WebSocket(url);
  } catch {
    handlers.onError("Could not open a live connection to the investigation service.");
    handlers.onClose();
    return () => {};
  }

  socket.onopen = () => socket?.send(JSON.stringify(payload));
  socket.onmessage = (ev) => {
    try {
      handlers.onEvent(JSON.parse(ev.data) as InvestigationEvent);
    } catch {
      handlers.onError("Received an unreadable message from the server.");
    }
  };
  socket.onerror = () => {
    if (!closedByUs) handlers.onError("The investigation connection was interrupted.");
  };
  socket.onclose = () => handlers.onClose();

  return () => {
    closedByUs = true;
    socket?.close();
  };
}
