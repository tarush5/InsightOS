import Link from "next/link";
import { HeroDiagram } from "@/components/HeroDiagram";

/* The hero thesis: the pipeline itself. The product's claim is that it does the
   work between a question and a decision, so the hero shows that chain running
   rather than a screenshot or an abstract gradient. */

const PIPELINE = [
  { stage: "Understand", note: "Resolve the question against governed metrics" },
  { stage: "Investigate", note: "Generate, validate and execute read-only SQL" },
  { stage: "Analyze", note: "Profile, decompose, detect anomalies" },
  { stage: "Verify", note: "Check every claim against computed evidence" },
  { stage: "Predict", note: "Backtested forecasts with measured error" },
  { stage: "Recommend", note: "Actions tied to the drivers that moved" },
];

export default function LandingPage() {
  return (
    <main id="main" className="relative min-h-screen">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[38rem] grid-field" aria-hidden />

      <header className="relative mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <span className="font-display text-lg font-bold tracking-tight">
          Insight<span className="text-cyan">OS</span>
        </span>
        <nav className="flex items-center gap-6 text-sm text-ink-muted">
          <Link href="/investigate" className="transition-colors hover:text-ink">
            Investigate
          </Link>
          <Link
            href="/investigate"
            className="rounded-lg border border-hairline bg-elevated px-4 py-2 text-ink transition-colors hover:border-cyan/40"
          >
            Start investigation
          </Link>
        </nav>
      </header>

      <section className="relative mx-auto max-w-6xl px-6 pb-24 pt-16">
        <p className="label-mono mb-6">Autonomous decision intelligence</p>

        <h1 className="max-w-3xl font-display text-5xl font-bold leading-[1.05] tracking-tight sm:text-6xl">
          Turn business data into
          <span className="block text-cyan">autonomous decisions.</span>
        </h1>

        <p className="mt-6 max-w-xl text-lg leading-relaxed text-ink-muted">
          InsightOS investigates your data, discovers hidden patterns, predicts
          what happens next, and recommends what to do — showing its working at
          every step.
        </p>

        <div className="mt-9 flex flex-wrap gap-3">
          <Link
            href="/investigate"
            className="rounded-lg bg-cyan px-5 py-3 font-medium text-base transition-transform duration-200 hover:-translate-y-0.5"
          >
            Start investigation
          </Link>
          <Link
            href="#pipeline"
            className="rounded-lg border border-hairline px-5 py-3 font-medium text-ink-muted transition-colors hover:border-cyan/40 hover:text-ink"
          >
            Explore platform
          </Link>
        </div>

        <div className="mt-16">
          <HeroDiagram />
        </div>
      </section>

      <section id="pipeline" className="relative mx-auto max-w-6xl px-6 pb-24">
        <h2 className="font-display text-2xl font-bold tracking-tight">
          What happens between the question and the answer
        </h2>
        <p className="mt-2 max-w-2xl text-ink-muted">
          Every figure is computed in Python and checked before you see it. The
          language model plans and narrates; it never does the arithmetic.
        </p>

        <ol className="mt-10 grid gap-px overflow-hidden rounded-2xl border border-hairline bg-hairline sm:grid-cols-2 lg:grid-cols-3">
          {PIPELINE.map((step, i) => (
            <li key={step.stage} className="group bg-surface p-6 transition-colors hover:bg-elevated">
              <span className="label-mono">{String(i + 1).padStart(2, "0")}</span>
              <h3 className="mt-3 font-display text-lg font-medium">{step.stage}</h3>
              <p className="mt-2 text-sm leading-relaxed text-ink-muted">{step.note}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="relative mx-auto max-w-6xl px-6 pb-28">
        <div className="panel p-8">
          <h2 className="font-display text-xl font-bold">Built to be checkable</h2>
          <div className="mt-6 grid gap-6 sm:grid-cols-3">
            {[
              ["Read-only by construction", "Every generated statement passes an AST-level validator before it can reach a database. Writes, stacked statements and system catalogs are rejected, not sanitised."],
              ["Attribution that reconciles", "Segment contributions are checked to sum to the headline change. If the decomposition does not reconcile, the conclusion is suppressed."],
              ["Confidence, decomposed", "Data, statistical, model and reasoning confidence are scored separately, and the weakest one is named."],
            ].map(([title, body]) => (
              <div key={title}>
                <h3 className="font-medium">{title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-ink-muted">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <footer className="border-t border-hairline">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-8 text-sm text-ink-faint">
          <span>InsightOS — From business questions to autonomous decisions.</span>
          <span className="font-mono text-xs">v0.1.0</span>
        </div>
      </footer>
    </main>
  );
}
