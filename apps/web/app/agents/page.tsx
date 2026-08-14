"use client";

import { Shell, SignInRequired, PageHeading } from "../../components/Shell";
import { useSession } from "../../lib/session";

export default function AgentsPage() {
  const { status } = useSession();

  if (status === "unknown") return <Shell><div className="animate-pulseSoft">Loading...</div></Shell>;
  if (status === "signed-out") return <Shell><SignInRequired what="view agent architecture" /></Shell>;

  return <AgentsContent />;
}

const AGENTS = [
  {
    name: "Orchestrator",
    role: "Central Coordinator",
    desc: "Manages the 14-stage investigation pipeline, breaking down queries and routing to specialized agents.",
    icon: "⚡",
  },
  {
    name: "Data Agent",
    role: "Data Engineering",
    desc: "Handles schema discovery, generates dialect-specific SQL, and safely executes queries to retrieve data.",
    icon: "🗄️",
  },
  {
    name: "EDA Agent",
    role: "Exploratory Analysis",
    desc: "Performs univariate/bivariate profiling, finds distributions, and calculates correlations across datasets.",
    icon: "📊",
  },
  {
    name: "ML Agent",
    role: "Predictive Modeling",
    desc: "Automates model selection, trains predictive algorithms, and evaluates performance metrics.",
    icon: "🧠",
  },
  {
    name: "Forecasting Agent",
    role: "Time-Series Analysis",
    desc: "Analyzes trends and seasonality, generating backtested forecasts with confidence intervals.",
    icon: "📈",
  },
  {
    name: "Root Cause Agent",
    role: "Driver Analysis",
    desc: "Attributes metric changes to specific dimensional drivers and measures their relative contribution.",
    icon: "🔍",
  },
  {
    name: "Simulation Agent",
    role: "Scenario Planning",
    desc: "Conducts what-if scenarios and sensitivity analysis to model the impact of different strategic choices.",
    icon: "🔮",
  },
  {
    name: "Critic Agent",
    role: "Verification & Safety",
    desc: "Validates all findings against raw data, detecting hallucinations and enforcing analytical rigor.",
    icon: "🛡️",
  },
];

function AgentsContent() {
  return (
    <Shell>
      <PageHeading eyebrow="Architecture" title="Agent Control Center">
        Monitor the specialized AI agents that power InsightOS investigations.
      </PageHeading>

      <div className="mb-8 grid gap-4 md:grid-cols-3">
        <div className="panel bg-surface/50 text-center py-6 border border-hairline">
          <p className="text-[10px] uppercase tracking-widest text-ink-muted">Total Investigations</p>
          <p className="mt-2 font-display text-3xl text-cyan">1,204</p>
        </div>
        <div className="panel bg-surface/50 text-center py-6 border border-hairline">
          <p className="text-[10px] uppercase tracking-widest text-ink-muted">Avg Agent Latency</p>
          <p className="mt-2 font-display text-3xl">1.2s</p>
        </div>
        <div className="panel bg-surface/50 text-center py-6 border border-hairline">
          <p className="text-[10px] uppercase tracking-widest text-ink-muted">Success Rate</p>
          <p className="mt-2 font-display text-3xl text-ok">99.8%</p>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {AGENTS.map((agent) => (
          <div key={agent.name} className="panel flex flex-col h-full hover:border-cyan/50 transition-colors">
            <div className="flex items-center justify-between mb-4">
              <div className="text-2xl">{agent.icon}</div>
              <span className="flex items-center gap-1.5 rounded-full bg-ok/10 px-2 py-0.5 text-[10px] font-medium text-ok border border-ok/20">
                <div className="h-1.5 w-1.5 rounded-full bg-ok animate-pulseSoft" />
                Active
              </span>
            </div>
            
            <h3 className="font-display font-semibold text-lg">{agent.name}</h3>
            <p className="font-mono text-[10px] text-cyan mb-2 uppercase tracking-wider">{agent.role}</p>
            
            <p className="text-sm text-ink-muted flex-1 mt-2 leading-relaxed">
              {agent.desc}
            </p>
          </div>
        ))}
      </div>
    </Shell>
  );
}
