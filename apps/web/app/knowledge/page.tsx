"use client";

import { Shell, SignInRequired, PageHeading } from "@/components/Shell";
import { useSession } from "@/lib/session";
import { Info } from "lucide-react";

export default function KnowledgePage() {
  const { status } = useSession();

  if (status === "unknown") return null;
  if (status !== "signed-in") {
    return (
      <Shell>
        <SignInRequired what="view the knowledge graph" />
      </Shell>
    );
  }

  return (
    <Shell>
      <PageHeading eyebrow="Entity Relationships" title="Knowledge Graph">
        The knowledge graph maps relationships between business entities discovered from your connected data sources.
      </PageHeading>

      <div className="panel p-0 overflow-hidden relative min-h-[500px] bg-surface flex flex-col">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(34,211,238,0.05)_0%,transparent_70%)] pointer-events-none" />
        
        <div className="flex-1 w-full h-full relative p-8">
          <svg className="absolute inset-0 w-full h-full" style={{ minHeight: 500 }}>
            {/* Edges */}
            <line x1="50%" y1="30%" x2="30%" y2="50%" stroke="currentColor" className="text-hairline" strokeWidth="2" />
            <line x1="50%" y1="30%" x2="70%" y2="50%" stroke="currentColor" className="text-hairline" strokeWidth="2" />
            <line x1="30%" y1="50%" x2="50%" y2="70%" stroke="currentColor" className="text-hairline" strokeWidth="2" />
            <line x1="70%" y1="50%" x2="50%" y2="70%" stroke="currentColor" className="text-hairline" strokeWidth="2" />
            <line x1="50%" y1="70%" x2="50%" y2="90%" stroke="currentColor" className="text-hairline" strokeWidth="2" />
            
            {/* Edge Labels */}
            <text x="40%" y="38%" fill="currentColor" className="text-ink-muted text-[10px] font-mono" textAnchor="middle">purchases</text>
            <text x="60%" y="38%" fill="currentColor" className="text-ink-muted text-[10px] font-mono" textAnchor="middle">contains</text>
            <text x="40%" y="62%" fill="currentColor" className="text-ink-muted text-[10px] font-mono" textAnchor="middle">placed_in</text>
            <text x="60%" y="62%" fill="currentColor" className="text-ink-muted text-[10px] font-mono" textAnchor="middle">ships_to</text>
          </svg>

          {/* Nodes */}
          <div className="absolute top-[30%] left-[50%] -translate-x-1/2 -translate-y-1/2 z-10">
            <Node label="Customers" />
          </div>
          <div className="absolute top-[50%] left-[30%] -translate-x-1/2 -translate-y-1/2 z-10">
            <Node label="Orders" highlight />
          </div>
          <div className="absolute top-[50%] left-[70%] -translate-x-1/2 -translate-y-1/2 z-10">
            <Node label="Products" />
          </div>
          <div className="absolute top-[70%] left-[50%] -translate-x-1/2 -translate-y-1/2 z-10">
            <Node label="Regions" />
          </div>
          <div className="absolute top-[90%] left-[50%] -translate-x-1/2 -translate-y-1/2 z-10">
            <Node label="Metrics" />
          </div>
        </div>

        <div className="bg-elevated/50 border-t border-hairline p-4 flex items-start gap-3 relative z-20">
          <Info className="h-5 w-5 text-cyan shrink-0 mt-0.5" />
          <p className="text-sm text-ink-muted">
            Note: Full interactive graph visualization coming soon. Entity relationships are used internally by the investigation engine to formulate semantic queries.
          </p>
        </div>
      </div>
    </Shell>
  );
}

function Node({ label, highlight = false }: { label: string; highlight?: boolean }) {
  return (
    <div className={`flex items-center justify-center h-20 w-20 rounded-full border-2 shadow-lg backdrop-blur-md transition-transform hover:scale-110 cursor-default ${
      highlight 
        ? "border-cyan bg-cyan/10 text-cyan shadow-cyan/20" 
        : "border-ink-faint bg-elevated/80 text-ink shadow-black/50"
    }`}>
      <span className="font-mono text-xs font-medium">{label}</span>
    </div>
  );
}
