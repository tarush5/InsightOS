"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AgentTrace } from "@/components/AgentTrace";
import { ConfidencePanel } from "@/components/ConfidencePanel";
import { DriverChart } from "@/components/DriverChart";
import { EvidenceDrawer } from "@/components/EvidenceDrawer";
import { api, streamInvestigation } from "@/lib/api";
import type { DemoScenario, InvestigationEvent, InvestigationResult } from "@/lib/types";

type Status = "idle" | "running" | "done" | "error";

export default function InvestigatePage() {
  const [question, setQuestion] = useState("");
  const [scenarios, setScenarios] = useState<DemoScenario[]>([]);
  const [events, setEvents] = useState<InvestigationEvent[]>([]);
  const [result, setResult] = useState<InvestigationResult | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [degraded, setDegraded] = useState<string | null>(null);
  const cancelRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    api.demoScenarios().then((r) => setScenarios(r.scenarios)).catch(() => setScenarios([]));
    api.capabilities()
      .then((c) => setDegraded(c.degraded_mode ? c.degraded_note : null))
      .catch(() => {});
    return () => cancelRef.current?.();
  }, []);

  const run = useCallback(async (payload: Record<string, unknown>) => {
    cancelRef.current?.();
    setEvents([]);
    setResult(null);
    setError(null);
    setStatus("running");

    // The ticket has to be fetched before the socket opens, so opening is async.
    cancelRef.current = await streamInvestigation(payload, {
      onEvent: (event) => {
        setEvents((prev) => [...prev, event]);
        if (event.state === "failed") {
          setError(String(event.detail.error ?? "The investigation could not be completed."));
          setStatus("error");
        } else if (event.stage === "complete" && event.state === "done") {
          setResult(event.detail as unknown as InvestigationResult);
          setStatus("done");
        }
      },
      onError: (message) => {
        setError(message);
        setStatus("error");
      },
      onClose: () => setStatus((s) => (s === "running" ? "done" : s)),
    });
  }, []);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (question.trim().length < 5) return;
    const match = scenarios.find((s) => s.question === question.trim());
    void run(match ? { ...match } : { question: question.trim() });
  };

  return (
    <main id="main" className="mx-auto min-h-screen max-w-6xl px-6 py-8">
      <header className="mb-8 flex items-center justify-between">
        <Link href="/" className="font-display text-lg font-bold tracking-tight">
          Insight<span className="text-cyan">OS</span>
        </Link>
        {status === "running" && (
          <span className="label-mono animate-pulseSoft text-cyan">Investigating</span>
        )}
      </header>

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
            className="flex-1 rounded-lg border border-hairline bg-surface px-4 py-3 text-ink placeholder:text-ink-faint focus:border-cyan/40"
          />
          <button
            type="submit"
            disabled={status === "running" || question.trim().length < 5}
            className="rounded-lg bg-cyan px-6 py-3 font-medium text-base transition-opacity disabled:opacity-40"
          >
            {status === "running" ? "Running…" : "Investigate"}
          </button>
        </div>
      </form>

      {scenarios.length > 0 && status === "idle" && (
        <div className="mt-5">
          <p className="label-mono mb-3">Or start from a prepared scenario</p>
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
          <span className="text-warn">Deterministic mode.</span> {degraded}
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

                {result.drivers && <DriverChart drivers={result.drivers} />}

                {result.recommendations && result.recommendations.length > 0 && (
                  <section className="panel p-6">
                    <h2 className="label-mono mb-4">Recommendations</h2>
                    <ol className="space-y-4">
                      {result.recommendations.map((r) => (
                        <li key={r.priority} className="border-l-2 border-violet/50 pl-4">
                          <p className="font-medium">{r.recommendation}</p>
                          <p className="mt-1 text-sm text-ink-muted">{r.problem}</p>
                          <p className="mt-2 font-mono text-xs text-ink-faint">
                            est. recovery {(r.expected_impact_pct * 100).toFixed(2)}% ·
                            risk {r.risk} · {r.confidence}
                          </p>
                          <details className="mt-2">
                            <summary className="cursor-pointer text-xs text-ink-faint hover:text-ink-muted">
                              Assumptions
                            </summary>
                            <ul className="mt-1 list-disc pl-4 text-xs text-ink-faint">
                              {r.assumptions.map((a) => <li key={a}>{a}</li>)}
                            </ul>
                          </details>
                        </li>
                      ))}
                    </ol>
                  </section>
                )}

                {result.evidence && <EvidenceDrawer evidence={result.evidence} />}
              </>
            )}
          </div>
        </div>
      )}
    </main>
  );
}
