"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  LayoutDashboard,
  Search,
  MessageSquare,
  Database,
  Table2,
  Layers,
  Brain,
  FlaskConical,
  GitBranch,
  Bot,
  FileText,
  Bell,
  CheckCircle2,
  Activity,
  Shield,
  PlusCircle,
  FilePlus,
  PlayCircle
} from "lucide-react";

type Command = {
  id: string;
  name: string;
  icon: React.ReactNode;
  group: string;
  action: () => void;
};

export function CommandPalette({ isOpen, setIsOpen }: { isOpen: boolean; setIsOpen: (o: boolean) => void }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "k") {
        e.preventDefault();
        setIsOpen(true);
      } else if (e.key === "Escape" && isOpen) {
        setIsOpen(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, setIsOpen]);

  useEffect(() => {
    if (isOpen) {
      setQuery("");
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [isOpen]);

  const commands: Command[] = [
    // Navigation
    { id: "nav-dashboard", name: "Dashboard", group: "Navigation", icon: <LayoutDashboard className="h-4 w-4" />, action: () => router.push("/dashboard") },
    { id: "nav-investigate", name: "Investigate", group: "Navigation", icon: <Search className="h-4 w-4" />, action: () => router.push("/investigate") },
    { id: "nav-ask", name: "Ask", group: "Navigation", icon: <MessageSquare className="h-4 w-4" />, action: () => router.push("/query") },
    { id: "nav-data", name: "Data Sources", group: "Navigation", icon: <Database className="h-4 w-4" />, action: () => router.push("/data") },
    { id: "nav-datasets", name: "Datasets", group: "Navigation", icon: <Table2 className="h-4 w-4" />, action: () => router.push("/datasets") },
    { id: "nav-semantic", name: "Semantic Layer", group: "Navigation", icon: <Layers className="h-4 w-4" />, action: () => router.push("/semantic-layer") },
    { id: "nav-models", name: "Models", group: "Navigation", icon: <Brain className="h-4 w-4" />, action: () => router.push("/models") },
    { id: "nav-simulation", name: "Simulation", group: "Navigation", icon: <FlaskConical className="h-4 w-4" />, action: () => router.push("/simulation") },
    { id: "nav-causal", name: "Causal", group: "Navigation", icon: <GitBranch className="h-4 w-4" />, action: () => router.push("/causal") },
    { id: "nav-agents", name: "Agents", group: "Navigation", icon: <Bot className="h-4 w-4" />, action: () => router.push("/agents") },
    { id: "nav-reports", name: "Reports", group: "Navigation", icon: <FileText className="h-4 w-4" />, action: () => router.push("/reports") },
    { id: "nav-alerts", name: "Alerts", group: "Navigation", icon: <Bell className="h-4 w-4" />, action: () => router.push("/alerts") },
    { id: "nav-eval", name: "Evaluation", group: "Navigation", icon: <CheckCircle2 className="h-4 w-4" />, action: () => router.push("/evaluation") },
    { id: "nav-obs", name: "Observability", group: "Navigation", icon: <Activity className="h-4 w-4" />, action: () => router.push("/observability") },
    { id: "nav-sec", name: "Security", group: "Navigation", icon: <Shield className="h-4 w-4" />, action: () => router.push("/security") },
    
    // Quick Commands
    { id: "cmd-new-inv", name: "New Investigation", group: "Quick Commands", icon: <Search className="h-4 w-4" />, action: () => router.push("/investigate/new") },
    { id: "cmd-new-data", name: "Connect Data Source", group: "Quick Commands", icon: <PlusCircle className="h-4 w-4" />, action: () => router.push("/data/new") },
    { id: "cmd-new-model", name: "Train Model", group: "Quick Commands", icon: <Brain className="h-4 w-4" />, action: () => router.push("/models/new") },
    { id: "cmd-run-sim", name: "Run Simulation", group: "Quick Commands", icon: <PlayCircle className="h-4 w-4" />, action: () => router.push("/simulation/new") },
    { id: "cmd-new-report", name: "Generate Report", group: "Quick Commands", icon: <FilePlus className="h-4 w-4" />, action: () => router.push("/reports/new") },
  ];

  const filteredCommands = commands.filter(cmd =>
    cmd.name.toLowerCase().includes(query.toLowerCase())
  );

  const groups = Array.from(new Set(filteredCommands.map(c => c.group)));

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev + 1) % filteredCommands.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev - 1 + filteredCommands.length) % filteredCommands.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (filteredCommands[selectedIndex]) {
        filteredCommands[selectedIndex].action();
        setIsOpen(false);
      }
    }
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-base/80 backdrop-blur-sm"
            onClick={() => setIsOpen(false)}
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: -20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: -20 }}
            transition={{ duration: 0.15 }}
            className="fixed left-1/2 top-[15%] z-50 w-full max-w-xl -translate-x-1/2 overflow-hidden rounded-2xl border border-hairline bg-surface shadow-2xl"
          >
            <div className="flex items-center border-b border-hairline px-4">
              <Search className="h-5 w-5 text-ink-muted" />
              <input
                ref={inputRef}
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Search commands..."
                className="w-full bg-transparent px-4 py-4 text-sm text-ink outline-none placeholder:text-ink-muted"
              />
              <span className="rounded bg-elevated px-2 py-1 font-mono text-[10px] text-ink-muted">ESC</span>
            </div>

            <div className="max-h-[60vh] overflow-y-auto p-2">
              {filteredCommands.length === 0 ? (
                <div className="py-8 text-center text-sm text-ink-muted">No commands found.</div>
              ) : (
                groups.map(group => (
                  <div key={group} className="mb-4 last:mb-0">
                    <div className="mb-2 px-2 font-mono text-xs font-semibold tracking-wider text-ink-faint">
                      {group}
                    </div>
                    <div className="space-y-1">
                      {filteredCommands.filter(c => c.group === group).map(cmd => {
                        const index = filteredCommands.indexOf(cmd);
                        const isSelected = index === selectedIndex;
                        return (
                          <div
                            key={cmd.id}
                            onClick={() => {
                              cmd.action();
                              setIsOpen(false);
                            }}
                            onMouseEnter={() => setSelectedIndex(index)}
                            className={`flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                              isSelected ? "bg-cyan/10 text-cyan" : "text-ink hover:bg-elevated/60"
                            }`}
                          >
                            <div className={isSelected ? "text-cyan" : "text-ink-muted"}>
                              {cmd.icon}
                            </div>
                            {cmd.name}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ))
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
