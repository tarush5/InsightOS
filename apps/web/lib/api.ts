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

  history: (limit = 25, offset = 0) =>
    request<{ total: number; investigations: HistoryRow[] }>(
      `/investigations/history?limit=${limit}&offset=${offset}`,
    ),

  investigation: (reference: string) =>
    request<Record<string, unknown>>(`/investigations/${reference}`),

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

  diffInDiff: (payload: Record<string, unknown>) =>
    request<CausalResponse>("/analysis/causal/diff-in-diff", json(payload)),

  simulate: (payload: Record<string, unknown>) =>
    request<Record<string, unknown>>("/analysis/simulate", json(payload)),

  dataSources: () =>
    request<{ data_sources: DataSourceSummary[] }>("/datasources"),

  crawlDataSource: (id: string) =>
    request<Record<string, unknown>>(`/datasources/${id}/crawl`, { method: "POST" }),

  /** Generates and validates SQL without running it. */
  explainQuery: (question: string, dataSourceId: string) =>
    request<AskResult>("/query/explain", json({ question, data_source_id: dataSourceId })),

  askQuery: (question: string, dataSourceId: string) =>
    request<AskResult>("/query/ask", json({ question, data_source_id: dataSourceId })),

  documents: () => request<{ documents: DocumentRecord[] }>("/documents"),

  ingestDocument: (title: string, text: string) =>
    request<DocumentRecord & { warning?: string }>("/documents", json({ title, text })),

  searchDocuments: (query: string, topK = 5) =>
    request<DocumentSearchResult>("/documents/search", json({ query, top_k: topK })),

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
