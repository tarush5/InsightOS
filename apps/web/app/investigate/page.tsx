"use client";

import { useCallback, useEffect, useRef, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Shell, SignInRequired, PageHeading } from "@/components/Shell";
import { AgentTrace } from "@/components/AgentTrace";
import { ConfidencePanel } from "@/components/ConfidencePanel";
import { DriverChart } from "@/components/DriverChart";
import { EvidenceDrawer } from "@/components/EvidenceDrawer";
import { ForecastChart } from "@/components/ForecastChart";
import { WaterfallChart } from "@/components/WaterfallChart";
import { api, streamInvestigation } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { DemoScenario, InvestigationEvent, InvestigationResult } from "@/lib/types";

type Status = "idle" | "running" | "done" | "error";

function InvestigateContent() {
  const { status: authStatus } = useSession();
  const searchParams = useSearchParams();

  const [question, setQuestion] = useState("");
  const [scenarios, setScenarios] = useState<DemoScenario[]>([]);
  const [events, setEvents] = useState<InvestigationEvent[]>([]);
  const [result, setResult] = useState<InvestigationResult | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [degraded, setDegraded] = useState<string | null>(null);
  const [reference, setReference] = useState<string | null>(null);
  const cancelRef = useRef<(() => void) | null>(null);

  const loadPastInvestigation = useCallback(async (ref: string) => {
    setStatus("running");
    setReference(ref);
    try {
      const data = await api.investigation(ref) as unknown as { timeline: Record<string, unknown>[], result: InvestigationResult };
      setEvents(data.timeline as unknown as InvestigationEvent[]);
      setResult(data.result);
      setStatus("done");
    } catch (err: any) {
      setError(err.message || "Failed to load past investigation");
      setStatus("error");
    }
  }, []);

  const run = useCallback(async (payload: Record<string, unknown>) => {
    cancelRef.current?.();
    setEvents([]);
    setResult(null);
    setError(null);
    setStatus("running");
    setReference(null);

    let eventReceived = false;

    cancelRef.current = await streamInvestigation(payload, {
      onEvent: (event) => {
        eventReceived = true;
        if (event.investigation_id) {
          setReference(event.investigation_id);
        }
        setEvents((prev) => [...prev, event]);
        if (event.state === "failed") {
          setError(String(event.detail.error ?? "The investigation could not be completed."));
          setStatus("error");
        } else if (event.stage === "complete" && event.state === "done") {
          setResult(event.detail as unknown as InvestigationResult);
          setStatus("done");
        }
      },
      onError: async (message) => {
        if (!eventReceived) {
          // Fallback to HTTP POST
          try {
            const data = await api.investigate(payload as any);
            setReference(data.reference);
            setEvents(data.timeline as unknown as InvestigationEvent[]);
            setResult(data.result as unknown as InvestigationResult);
            setStatus("done");
          } catch (err: any) {
            setError(err.message || message);
            setStatus("error");
          }
        } else {
          setError(message);
          setStatus("error");
        }
      },
      onClose: () => setStatus((s) => (s === "running" ? "done" : s)),
    });
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([
      api.demoScenarios().catch(() => ({ scenarios: [] })),
      api.capabilities().catch(() => ({ degraded_mode: false, degraded_note: null }))
    ]).then(([scenRes, capRes]) => {
      if (!active) return;
      setScenarios(scenRes.scenarios);
      if (capRes.degraded_mode) {
        setDegraded(capRes.degraded_note);
      }

      const q = searchParams.get("q");
      const scenarioId = searchParams.get("scenario");
      const ref = searchParams.get("ref");

      if (ref) {
        void loadPastInvestigation(ref);
      } else if (scenarioId) {
        const match = scenRes.scenarios.find(s => s.id === scenarioId);
        if (match) {
          setQuestion(match.question);
          void run({ ...match });
        }
      } else if (q) {
        setQuestion(q);
      }
    });

    return () => {
      active = false;
      cancelRef.current?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (question.trim().length < 5) return;
    const match = scenarios.find((s) => s.question === question.trim());
    void run(match ? { ...match } : { question: question.trim() });
  };

  if (authStatus === "unknown") return null;
  if (authStatus === "signed-out") {
    return (
      <Shell>
        <SignInRequired what="run investigations" />
      </Shell>
    );
  }

  const durationSec = events.length > 0 ? ((events[events.length - 1]?.elapsed_ms ?? 0) / 1000).toFixed(1) : null;
  const tokenUsage = result?.usage ? result.usage.total_tokens : null;

  return (
    <Shell>
      <div className="flex justify-between items-start">
        <PageHeading eyebrow="Core" title="Investigation" />
        {reference && status === "done" && (
          <button
            onClick={() => window.open(`/api/v1/investigations/${reference}/export?format=markdown`, '_blank')}
            className="rounded-lg bg-surface px-4 py-2 text-sm font-medium text-ink transition-colors hover:text-cyan border border-hairline"
          >
            Export Report
          </button>
        )}
      </div>

      <div className="mx-auto w-full pb-12">
        <h1 className="font-display text-3xl font-bold tracking-tight">
          What would you like to understand?
        </h1>

        <form onSubmit={submit} className="mt-6">
          <label htmlFor="question" className="sr-only">Your question</label>
          <div className="flex flex-col gap-3 sm:flex-row">
            <input
              id="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Why did revenue decrease in August?"
              className="flex-1 rounded-lg border border-hairline bg-surface px-4 py-3 text-ink placeholder:text-ink-faint focus:border-cyan/40 focus:outline-none"
            />
            <button
              type="submit"
              disabled={status === "running" || question.trim().length < 5}
              className="rounded-lg bg-cyan px-6 py-3 font-medium text-base text-surface transition-opacity disabled:opacity-40"
            >
              {status === "running" ? "Running…" : "Investigate"}
            </button>
          </div>
        </form>

        {scenarios.length > 0 && status === "idle" && (
          <div className="mt-5">
            <p className="label-mono mb-3 text-ink-muted">Or start from a prepared scenario</p>
            <div className="flex flex-wrap gap-2">
              {scenarios.map((s) => (
                <button
                  key={s.id}
                  onClick={() => { setQuestion(s.question); void run({ ...s }); }}
                  className="rounded-lg border border-hairline bg-surface px-3 py-2 text-sm text-ink-muted transition-colors hover:border-cyan/40 hover:text-ink"
                >
                  {s.question}
                </button>
              ))}
            </div>
          </div>
        )}

        {degraded && (
          <p className="mt-6 rounded-lg border border-warn/30 bg-warn/5 px-4 py-3 text-sm text-ink-muted">
            <span className="text-warn font-medium">Deterministic mode.</span> {degraded}
          </p>
        )}

        {error && (
          <div className="mt-6 rounded-lg border border-crit/30 bg-crit/5 px-4 py-3">
            <p className="text-sm text-ink">{error}</p>
            <button
              onClick={() => setStatus("idle")}
              className="mt-2 text-sm text-cyan underline underline-offset-4"
            >
              Try again
            </button>
          </div>
        )}

        {status === "idle" && scenarios.length === 0 && !error && (
          <div className="panel mt-10 p-10 text-center">
            <p className="text-ink-muted">No warehouse is connected yet.</p>
            <p className="mt-1 text-sm text-ink-faint">
              Run <code className="font-mono text-cyan">python scripts/seed_data.py --out ./seed</code>{" "}
              to generate the demo dataset, then restart the API.
            </p>
          </div>
        )}

        {events.length > 0 && (
          <div className="mt-10 grid gap-6 lg:grid-cols-[22rem_1fr]">
            <div className="space-y-6">
              <AgentTrace events={events} />
              {result?.confidence && (
                <ConfidencePanel confidence={result.confidence} critic={result.critic} />
              )}
              {reference && (
                <div className="panel p-4 flex flex-col gap-2 border-hairline">
                  <div className="flex justify-between items-center text-sm">
                    <span className="text-ink-muted">Reference</span>
                    <span className="font-mono text-ink text-xs">{reference}</span>
                  </div>
                  {durationSec && (
                    <div className="flex justify-between items-center text-sm">
                      <span className="text-ink-muted">Duration</span>
                      <span className="font-mono text-ink text-xs">{durationSec}s</span>
                    </div>
                  )}
                  {tokenUsage != null && (
                    <div className="flex justify-between items-center text-sm">
                      <span className="text-ink-muted">Tokens</span>
                      <span className="font-mono text-ink text-xs">{tokenUsage.toLocaleString()}</span>
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="space-y-6">
              {result && (
                <>
                  <section className="panel p-6 animate-riseIn">
                    <h2 className="label-mono mb-3">
                      {result.verdict === "answered" ? "Executive summary" : "Verdict"}
                    </h2>
                    <p className="font-display text-2xl font-medium leading-snug">
                      {result.headline}
                    </p>
                    {result.narrative && (
                      <p className="mt-4 leading-relaxed text-ink-muted">{result.narrative}</p>
                    )}
                  </section>

                  {result.forecast && (
                    <section className="panel p-6 animate-riseIn">
                      <h2 className="label-mono mb-4 text-ink-muted">Forecast</h2>
                      <ForecastChart forecast={result.forecast} />
                    </section>
                  )}

                  {result.drivers && result.drivers.length > 0 && (
                    <section className="panel p-6 animate-riseIn">
                      <h2 className="label-mono mb-4 text-ink-muted">Driver Analysis</h2>
                      <WaterfallChart drivers={result.drivers} />
                      <div className="mt-8 pt-6 border-t border-hairline">
                        <h3 className="label-mono mb-4 text-ink-faint">Detailed View</h3>
                        <DriverChart drivers={result.drivers} />
                      </div>
                    </section>
                  )}

                  {result.recommendations && result.recommendations.length > 0 && (
                    <section className="panel p-6 animate-riseIn">
                      <h2 className="label-mono mb-4 text-ink-muted">Recommendations</h2>
                      <ol className="space-y-4">
                        {result.recommendations.map((r) => (
                          <li key={r.priority} className="border-l-2 border-violet/50 pl-4">
                            <p className="font-medium text-ink">{r.recommendation}</p>
                            <p className="mt-1 text-sm text-ink-muted">{r.problem}</p>
                            <p className="mt-2 font-mono text-xs text-ink-faint">
                              est. recovery {(r.expected_impact_pct * 100).toFixed(2)}% ·
                              risk {r.risk} · {r.confidence}
                            </p>
                            <details className="mt-2 group">
                              <summary className="cursor-pointer text-xs text-ink-faint hover:text-ink-muted list-none">
                                <span className="underline decoration-hairline underline-offset-2 group-open:no-underline">Assumptions</span>
                              </summary>
                              <ul className="mt-2 list-disc pl-4 text-xs text-ink-faint space-y-1">
                                {r.assumptions.map((a) => <li key={a}>{a}</li>)}
                              </ul>
                            </details>
                          </li>
                        ))}
                      </ol>
                    </section>
                  )}

                  {result.evidence && (
                    <div className="animate-riseIn">
                      <EvidenceDrawer evidence={result.evidence} />
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </Shell>
  );
}

export default function InvestigatePage() {
  return (
    <Suspense fallback={<div className="flex h-screen items-center justify-center text-ink-muted">Loading...</div>}>
      <InvestigateContent />
    </Suspense>
  );
}
