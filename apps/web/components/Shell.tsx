"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "@/lib/session";
import { useState, useEffect } from "react";
import { CommandPalette } from "./CommandPalette";
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
  ChevronLeft,
  ChevronRight,
  LogOut,
  Search as SearchIcon,
} from "lucide-react";

const NAV_GROUPS = [
  {
    label: "Core",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/investigate", label: "Investigate", icon: Search },
      { href: "/query", label: "Ask", icon: MessageSquare },
    ],
  },
  {
    label: "Data & Metrics",
    items: [
      { href: "/data", label: "Data Sources", icon: Database },
      { href: "/datasets", label: "Datasets", icon: Table2 },
      { href: "/semantic-layer", label: "Semantic Layer", icon: Layers },
    ],
  },
  {
    label: "Analytics & AI",
    items: [
      { href: "/models", label: "Models", icon: Brain },
      { href: "/simulation", label: "Simulation", icon: FlaskConical },
      { href: "/causal", label: "Causal", icon: GitBranch },
      { href: "/agents", label: "Agents", icon: Bot },
    ],
  },
  {
    label: "Governance",
    items: [
      { href: "/reports", label: "Reports", icon: FileText },
      { href: "/alerts", label: "Alerts", icon: Bell },
      { href: "/evaluation", label: "Evaluation", icon: CheckCircle2 },
      { href: "/observability", label: "Observability", icon: Activity },
      { href: "/security", label: "Security", icon: Shield },
    ],
  },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { identity, status, signOut } = useSession();
  
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isMounted, setIsMounted] = useState(false);
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState(false);

  useEffect(() => {
    setIsMounted(true);
    const stored = localStorage.getItem("insightos-sidebar-collapsed");
    if (stored !== null) {
      setIsCollapsed(stored === "true");
    }
  }, []);

  const toggleCollapse = () => {
    setIsCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem("insightos-sidebar-collapsed", String(next));
      return next;
    });
  };

  return (
    <div className="min-h-screen bg-base text-ink flex">
      {/* Sidebar */}
      <aside
        className={`sticky top-0 h-screen flex flex-col border-r border-hairline bg-surface/50 backdrop-blur transition-all duration-300 z-40 ${
          isCollapsed ? "w-16" : "w-[240px]"
        }`}
      >
        <div className={`flex items-center h-16 ${isCollapsed ? "justify-center px-0" : "px-6"}`}>
          <Link href="/" className="font-display text-sm font-bold tracking-tight whitespace-nowrap overflow-hidden">
            {isCollapsed ? (
              <span className="text-cyan">OS</span>
            ) : (
              <>Insight<span className="text-cyan">OS</span></>
            )}
          </Link>
        </div>

        <div className="flex-1 overflow-y-auto overflow-x-hidden py-4 no-scrollbar">
          <div className="px-3 mb-6">
            <button
              onClick={() => setIsCommandPaletteOpen(true)}
              className={`w-full flex items-center gap-2 rounded-lg border border-hairline bg-elevated/30 p-2 text-ink-muted transition-colors hover:border-ink-faint hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan ${isCollapsed ? "justify-center" : ""}`}
            >
              <SearchIcon className="h-4 w-4 shrink-0" />
              {!isCollapsed && (
                <>
                  <span className="text-xs font-medium">Search...</span>
                  <span className="ml-auto rounded bg-base px-1.5 py-0.5 font-mono text-[10px]">Ctrl K</span>
                </>
              )}
            </button>
          </div>

          <nav className="space-y-6 px-3" aria-label="Main">
            {NAV_GROUPS.map((group) => (
              <div key={group.label}>
                {!isCollapsed && (
                  <div className="px-3 mb-2 font-mono text-[10px] uppercase tracking-wider text-ink-faint">
                    {group.label}
                  </div>
                )}
                <div className="space-y-1">
                  {group.items.map((route) => {
                    const active = pathname === route.href || pathname.startsWith(`${route.href}/`);
                    const Icon = route.icon;
                    return (
                      <Link
                        key={route.href}
                        href={route.href}
                        title={isCollapsed ? route.label : undefined}
                        aria-current={active ? "page" : undefined}
                        className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan ${
                          active
                            ? "bg-cyan/10 text-cyan font-medium"
                            : "text-ink-muted hover:bg-elevated hover:text-ink"
                        } ${isCollapsed ? "justify-center" : ""}`}
                      >
                        <Icon className="h-4 w-4 shrink-0" />
                        {!isCollapsed && <span>{route.label}</span>}
                      </Link>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>
        </div>

        <div className="mt-auto border-t border-hairline p-3 space-y-2">
          {status === "signed-in" && identity ? (
            <div className={`flex flex-col gap-2 ${isCollapsed ? "items-center" : ""}`}>
              {!isCollapsed && (
                <div className="px-3 py-1 font-mono text-[10px] text-ink-faint truncate">
                  {identity.role} · {identity.workspace_id.slice(0, 8)}
                </div>
              )}
              <button
                type="button"
                onClick={() => void signOut()}
                title={isCollapsed ? "Sign out" : undefined}
                className={`flex items-center gap-3 rounded-lg p-2 text-sm text-ink-muted transition-colors hover:bg-elevated hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan ${isCollapsed ? "justify-center w-full" : "px-3 w-full"}`}
              >
                <LogOut className="h-4 w-4 shrink-0" />
                {!isCollapsed && <span>Sign out</span>}
              </button>
            </div>
          ) : (
            <Link
              href="/login"
              className={`flex items-center gap-3 rounded-lg bg-cyan-dim/20 p-2 text-sm text-cyan transition-colors hover:bg-cyan-dim/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan ${isCollapsed ? "justify-center w-full" : "px-3 w-full"}`}
            >
              <LogOut className="h-4 w-4 shrink-0" />
              {!isCollapsed && <span>Sign in</span>}
            </Link>
          )}

          <button
            onClick={toggleCollapse}
            className={`flex items-center rounded-lg p-2 text-ink-muted transition-colors hover:bg-elevated hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan ${isCollapsed ? "justify-center w-full" : "ml-auto"}`}
          >
            {isCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
          </button>
        </div>
      </aside>

      <main id="main" className="flex-1 w-full overflow-hidden">
        <div className="mx-auto max-w-6xl px-6 py-10 h-full overflow-y-auto">
          {children}
        </div>
      </main>
      
      {isMounted && (
        <CommandPalette isOpen={isCommandPaletteOpen} setIsOpen={setIsCommandPaletteOpen} />
      )}
    </div>
  );
}

/** Shown wherever a page needs a session it does not have. */
export function SignInRequired({ what }: { what: string }) {
  return (
    <div className="rounded-2xl border border-hairline bg-surface p-8">
      <h2 className="font-display text-lg font-bold">Sign in to {what}</h2>
      <p className="mt-2 max-w-prose text-sm text-ink-muted">
        Every workspace sees only its own data, so this page needs a session. Creating a
        workspace takes one form and no email confirmation.
      </p>
      <Link
        href="/login"
        className="mt-5 inline-block rounded-lg bg-cyan px-4 py-2 font-mono text-xs font-medium text-base transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan"
      >
        Sign in or create a workspace
      </Link>
    </div>
  );
}

export function PageHeading({
  eyebrow,
  title,
  children,
}: {
  eyebrow: string;
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="mb-8">
      <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-cyan">{eyebrow}</p>
      <h1 className="mt-2 font-display text-3xl font-bold tracking-tight">{title}</h1>
      {children ? (
        <p className="mt-3 max-w-prose text-sm leading-relaxed text-ink-muted">{children}</p>
      ) : null}
    </div>
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-hairline p-10 text-center">
      <p className="font-display text-base font-semibold">{title}</p>
      <p className="mx-auto mt-2 max-w-md text-sm text-ink-muted">{body}</p>
    </div>
  );
}

export function ErrorNote({ message, fix }: { message: string; fix?: string }) {
  return (
    <div role="alert" className="rounded-xl border border-crit/40 bg-crit/10 px-4 py-3">
      <p className="text-sm text-ink">{message}</p>
      {fix ? <p className="mt-1 font-mono text-xs text-ink-muted">{fix}</p> : null}
    </div>
  );
}

