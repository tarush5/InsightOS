"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession } from "@/lib/session";

const ROUTES = [
  { href: "/investigate", label: "Investigate" },
  { href: "/query", label: "Ask" },
  { href: "/documents", label: "Documents" },
  { href: "/history", label: "History" },
  { href: "/alerts", label: "Alerts" },
  { href: "/causal", label: "Causal" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { identity, status, signOut } = useSession();

  return (
    <div className="min-h-screen bg-base text-ink">
      <header className="sticky top-0 z-30 border-b border-hairline bg-base/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3">
          <Link href="/" className="font-display text-sm font-bold tracking-tight">
            Insight<span className="text-cyan">OS</span>
          </Link>

          <nav className="flex items-center gap-1" aria-label="Main">
            {ROUTES.map((route) => {
              const active = pathname === route.href || pathname.startsWith(`${route.href}/`);
              return (
                <Link
                  key={route.href}
                  href={route.href}
                  aria-current={active ? "page" : undefined}
                  className={`rounded-lg px-3 py-1.5 font-mono text-xs transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan ${
                    active
                      ? "bg-elevated text-ink"
                      : "text-ink-muted hover:bg-elevated/60 hover:text-ink"
                  }`}
                >
                  {route.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-3 font-mono text-xs">
            {status === "signed-in" && identity ? (
              <>
                <span className="text-ink-faint">
                  {identity.role} · {identity.workspace_id.slice(0, 8)}
                </span>
                <button
                  type="button"
                  onClick={() => void signOut()}
                  className="rounded-lg border border-hairline px-3 py-1.5 text-ink-muted transition-colors hover:border-ink-faint hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan"
                >
                  Sign out
                </button>
              </>
            ) : (
              <Link
                href="/login"
                className="rounded-lg border border-cyan-dim px-3 py-1.5 text-cyan transition-colors hover:bg-cyan-dim/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan"
              >
                Sign in
              </Link>
            )}
          </div>
        </div>
      </header>

      <main id="main" className="mx-auto max-w-6xl px-6 py-10">
        {children}
      </main>
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
