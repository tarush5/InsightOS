"use client";

/**
 * Session state.
 *
 * Tokens are held in memory and nowhere else. That is a deliberate trade: a
 * reload signs you out, which is worse UX than localStorage, but a token in
 * localStorage is readable by any script that gets injected into the page, and
 * this one carries a workspace claim. The right fix is httpOnly cookies set by
 * the API, which it does not yet issue — that gap is recorded in
 * docs/security.md rather than papered over here.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { ApiError, setAuthProvider } from "./api";
import type { Identity, TokenPair } from "./types";

interface SessionState {
  identity: Identity | null;
  status: "unknown" | "signed-out" | "signed-in";
  error: string | null;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (input: {
    email: string;
    password: string;
    fullName: string;
    workspaceName: string;
  }) => Promise<void>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionState | null>(null);

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}/api/v1${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = payload.detail ?? payload.message;
    throw new ApiError(
      typeof detail === "string" ? detail : (detail?.message ?? `Request failed (${res.status})`),
      res.status,
      payload.suggested_fix,
    );
  }
  return payload as T;
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const tokens = useRef<TokenPair | null>(null);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [status, setStatus] = useState<SessionState["status"]>("unknown");
  const [error, setError] = useState<string | null>(null);

  // The API client asks for the current token on every request rather than
  // capturing it, so a refresh mid-flight is picked up without re-rendering.
  useEffect(() => {
    setAuthProvider(() => tokens.current?.access_token ?? null);
    setStatus("signed-out");
  }, []);

  const adopt = useCallback(async (pair: TokenPair) => {
    tokens.current = pair;
    const me = await fetch(`${API_BASE}/api/v1/auth/me`, {
      headers: { Authorization: `Bearer ${pair.access_token}` },
    });
    if (!me.ok) throw new ApiError("Signed in, but could not load your profile.", me.status);
    setIdentity((await me.json()) as Identity);
    setStatus("signed-in");
    setError(null);
  }, []);

  const signIn = useCallback(
    async (email: string, password: string) => {
      setError(null);
      try {
        await adopt(await post<TokenPair>("/auth/login", { email, password }));
      } catch (err) {
        const message = err instanceof ApiError ? err.message : "Could not sign in.";
        setError(message);
        throw err;
      }
    },
    [adopt],
  );

  const signUp = useCallback<SessionState["signUp"]>(
    async ({ email, password, fullName, workspaceName }) => {
      setError(null);
      try {
        await adopt(
          await post<TokenPair>("/auth/signup", {
            email,
            password,
            full_name: fullName,
            workspace_name: workspaceName,
          }),
        );
      } catch (err) {
        const message = err instanceof ApiError ? err.message : "Could not create the workspace.";
        setError(message);
        throw err;
      }
    },
    [adopt],
  );

  const signOut = useCallback(async () => {
    const refresh = tokens.current?.refresh_token;
    const access = tokens.current?.access_token;
    tokens.current = null;
    setIdentity(null);
    setStatus("signed-out");
    if (refresh && access) {
      // Best effort: the local session is already gone either way, but the
      // server-side revocation is what stops a stolen refresh token.
      await fetch(`${API_BASE}/api/v1/auth/logout`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${access}` },
        body: JSON.stringify({ refresh_token: refresh }),
      }).catch(() => undefined);
    }
  }, []);

  const value = useMemo(
    () => ({ identity, status, error, signIn, signUp, signOut }),
    [identity, status, error, signIn, signUp, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession must be used inside SessionProvider");
  return context;
}
