"use client";

import { motion, useReducedMotion } from "framer-motion";

const NODES = [
  { id: "sources", label: "Data sources", x: 40, y: 110 },
  { id: "agents", label: "Agents", x: 210, y: 60 },
  { id: "models", label: "Models", x: 210, y: 160 },
  { id: "insights", label: "Insights", x: 390, y: 110 },
  { id: "decisions", label: "Decisions", x: 560, y: 110 },
] as const;

const EDGES: [string, string][] = [
  ["sources", "agents"], ["sources", "models"],
  ["agents", "insights"], ["models", "insights"],
  ["insights", "decisions"],
];

const at = (id: string) => NODES.find((n) => n.id === id)!;

/**
 * Abstract pipeline visualisation, drawn as SVG rather than an image so it
 * scales cleanly and respects reduced-motion. Particles travel the edges to
 * suggest flow; with reduced motion they are simply omitted.
 */
export function HeroDiagram() {
  const reduce = useReducedMotion();

  return (
    <div className="panel overflow-hidden p-4">
      <svg
        viewBox="0 0 660 220"
        className="h-auto w-full"
        role="img"
        aria-label="Data sources feed agents and models, which produce insights, which produce decisions."
      >
        <defs>
          <linearGradient id="edge" x1="0" x2="1">
            <stop offset="0%" stopColor="#22D3EE" stopOpacity="0.15" />
            <stop offset="100%" stopColor="#8B5CF6" stopOpacity="0.45" />
          </linearGradient>
        </defs>

        {EDGES.map(([from, to]) => {
          const a = at(from);
          const b = at(to);
          const d = `M ${a.x + 46} ${a.y} C ${(a.x + b.x) / 2 + 30} ${a.y}, ${(a.x + b.x) / 2 - 30} ${b.y}, ${b.x - 46} ${b.y}`;
          return (
            <g key={`${from}-${to}`}>
              <path d={d} fill="none" stroke="url(#edge)" strokeWidth={1.25} />
              {!reduce && (
                <circle r={2.5} fill="#22D3EE">
                  <animateMotion dur="3.2s" repeatCount="indefinite" path={d} />
                </circle>
              )}
            </g>
          );
        })}

        {NODES.map((n, i) => (
          <motion.g
            key={n.id}
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.4, delay: i * 0.09 }}
          >
            <rect
              x={n.x - 46} y={n.y - 18} width={92} height={36} rx={9}
              fill="#161923" stroke="#22252F"
            />
            <text
              x={n.x} y={n.y + 4} textAnchor="middle"
              className="fill-[#9BA1B0] font-mono"
              style={{ fontSize: 10, letterSpacing: "0.06em" }}
            >
              {n.label}
            </text>
          </motion.g>
        ))}
      </svg>
    </div>
  );
}
