"use client";

import { useEffect, useState } from "react";
import { Shell, SignInRequired, PageHeading } from "@/components/Shell";
import { useSession } from "@/lib/session";
import { api } from "@/lib/api";
import { User, Key, Shield, Info, ExternalLink } from "lucide-react";

export default function SettingsPage() {
  const { identity, status } = useSession();
  const [capabilities, setCapabilities] = useState<any>(null);

  useEffect(() => {
    if (status === "signed-in") {
      api.capabilities().then(setCapabilities).catch(console.error);
    }
  }, [status]);

  if (status === "unknown") return null;
  if (status !== "signed-in") {
    return (
      <Shell>
        <SignInRequired what="view settings" />
      </Shell>
    );
  }

  return (
    <Shell>
      <PageHeading eyebrow="Configuration" title="Workspace Settings">
        Manage your account, LLM providers, and query constraints.
      </PageHeading>

      <div className="max-w-3xl space-y-8">
        <section className="panel p-6">
          <h2 className="font-display font-semibold flex items-center gap-2 mb-6">
            <User className="h-5 w-5 text-cyan" /> Account
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
            <div>
              <label className="label-mono text-xs text-ink-muted block mb-1">User ID</label>
              <div className="font-medium">{identity?.user_id || "Unknown"}</div>
            </div>
            <div>
              <label className="label-mono text-xs text-ink-muted block mb-1">Role</label>
              <div className="font-medium capitalize">{identity?.role || "Unknown"}</div>
            </div>
            <div className="sm:col-span-2">
              <label className="label-mono text-xs text-ink-muted block mb-1">Workspace ID</label>
              <div className="font-mono text-sm bg-elevated p-2 rounded border border-hairline inline-block">
                {identity?.workspace_id || "Unknown"}
              </div>
            </div>
          </div>
        </section>

        <section className="panel p-6">
          <h2 className="font-display font-semibold flex items-center gap-2 mb-6">
            <Key className="h-5 w-5 text-cyan" /> LLM Configuration
          </h2>
          {capabilities ? (
            <div className="space-y-4">
              <div className="flex items-center justify-between p-3 border border-hairline rounded-lg bg-surface/50">
                <div>
                  <div className="font-medium">Primary Provider</div>
                  <div className="text-sm text-ink-muted">Using the configured default model</div>
                </div>
                <div className="px-3 py-1 bg-cyan/10 text-cyan rounded-full text-xs font-medium">
                  Active
                </div>
              </div>
              <div>
                <label className="label-mono text-xs text-ink-muted block mb-2">Available Capabilities</label>
                <div className="flex flex-wrap gap-2">
                  {capabilities.implemented.map((cap: string) => (
                    <span key={cap} className="px-2 py-1 bg-elevated border border-hairline rounded text-xs font-mono">
                      {cap}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="animate-pulseSoft h-20 bg-elevated rounded-lg" />
          )}
        </section>

        <section className="panel p-6">
          <h2 className="font-display font-semibold flex items-center gap-2 mb-6">
            <Shield className="h-5 w-5 text-cyan" /> Query Safety
          </h2>
          <div className="grid grid-cols-3 gap-4">
            <div className="p-4 border border-hairline rounded-lg bg-surface/50">
              <div className="text-2xl font-display font-bold">30s</div>
              <div className="text-xs text-ink-muted mt-1">Timeout</div>
            </div>
            <div className="p-4 border border-hairline rounded-lg bg-surface/50">
              <div className="text-2xl font-display font-bold">10k</div>
              <div className="text-xs text-ink-muted mt-1">Max Rows</div>
            </div>
            <div className="p-4 border border-hairline rounded-lg bg-surface/50">
              <div className="text-2xl font-display font-bold">5</div>
              <div className="text-xs text-ink-muted mt-1">Max Joins</div>
            </div>
          </div>
        </section>

        <section className="panel p-6 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Info className="h-5 w-5 text-cyan" />
            <div>
              <h2 className="font-display font-semibold">About InsightOS</h2>
              <p className="text-sm text-ink-muted">Version 0.1.0-beta</p>
            </div>
          </div>
          <button className="flex items-center gap-2 text-sm font-medium text-cyan hover:text-cyan-dim transition-colors">
            Documentation <ExternalLink className="h-4 w-4" />
          </button>
        </section>
      </div>
    </Shell>
  );
}
