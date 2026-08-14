"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ErrorNote, PageHeading, Shell } from "@/components/Shell";
import { useSession } from "@/lib/session";

type Mode = "sign-in" | "create";

const field =
  "w-full rounded-lg border border-hairline bg-elevated px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-cyan-dim focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan/40";
const label = "block font-mono text-[11px] uppercase tracking-[0.15em] text-ink-muted";

export default function LoginPage() {
  const router = useRouter();
  const { signIn, signUp, error, identity } = useSession();
  const [mode, setMode] = useState<Mode>("create");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    email: "",
    password: "",
    fullName: "",
    workspaceName: "",
  });

  const update = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((prev) => ({ ...prev, [key]: e.target.value }));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      if (mode === "sign-in") {
        await signIn(form.email, form.password);
      } else {
        await signUp(form);
      }
      router.push("/investigate");
    } catch {
      /* the session exposes the message; nothing to add here */
    } finally {
      setBusy(false);
    }
  };

  return (
    <Shell>
      <div className="mx-auto max-w-md">
        <PageHeading eyebrow="Access" title={mode === "create" ? "Create a workspace" : "Sign in"}>
          {mode === "create"
            ? "The first member of a workspace owns it. No email confirmation, no seat limit."
            : "Sessions live in memory only, so a reload signs you out."}
        </PageHeading>

        <form onSubmit={submit} className="space-y-4 rounded-2xl border border-hairline bg-surface p-6">
          {mode === "create" ? (
            <>
              <div>
                <label className={label} htmlFor="workspace">
                  Workspace name
                </label>
                <input
                  id="workspace"
                  required
                  minLength={2}
                  className={`${field} mt-1.5`}
                  placeholder="Northwind Trading"
                  value={form.workspaceName}
                  onChange={update("workspaceName")}
                />
              </div>
              <div>
                <label className={label} htmlFor="name">
                  Your name
                </label>
                <input
                  id="name"
                  className={`${field} mt-1.5`}
                  placeholder="Alex Rivera"
                  value={form.fullName}
                  onChange={update("fullName")}
                />
              </div>
            </>
          ) : null}

          <div>
            <label className={label} htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              className={`${field} mt-1.5`}
              placeholder="you@company.com"
              value={form.email}
              onChange={update("email")}
            />
          </div>

          <div>
            <label className={label} htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              minLength={mode === "create" ? 12 : 1}
              autoComplete={mode === "create" ? "new-password" : "current-password"}
              className={`${field} mt-1.5`}
              value={form.password}
              onChange={update("password")}
            />
            {mode === "create" ? (
              <p className="mt-1.5 font-mono text-[11px] text-ink-faint">
                12 characters minimum. Length beats punctuation.
              </p>
            ) : null}
          </div>

          {error ? <ErrorNote message={error} /> : null}

          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-lg bg-cyan px-4 py-2.5 font-mono text-xs font-medium text-base transition-opacity hover:opacity-90 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-cyan"
          >
            {busy ? "Working…" : mode === "create" ? "Create workspace" : "Sign in"}
          </button>

          <button
            type="button"
            onClick={() => setMode(mode === "create" ? "sign-in" : "create")}
            className="w-full text-center font-mono text-[11px] text-ink-muted underline-offset-4 hover:text-ink hover:underline"
          >
            {mode === "create" ? "I already have an account" : "Create a new workspace instead"}
          </button>
        </form>

        {identity ? (
          <p className="mt-4 text-center font-mono text-xs text-ok">
            Signed in as {identity.role}.
          </p>
        ) : null}
      </div>
    </Shell>
  );
}
