"use client";

import { Shell, SignInRequired, PageHeading, EmptyState } from "@/components/Shell";
import { useSession } from "@/lib/session";
import { Shield, Key, Users, Lock, Server, FileSearch, ShieldCheck, Database, Activity } from "lucide-react";

export default function SecurityPage() {
  const { status } = useSession();

  if (status === "unknown") return null;
  if (status !== "signed-in") {
    return (
      <Shell>
        <SignInRequired what="view security & governance" />
      </Shell>
    );
  }

  return (
    <Shell>
      <PageHeading eyebrow="Governance Center" title="Security & Governance">
        Manage roles, permissions, and security policies across your workspace.
      </PageHeading>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-10">
        <div className="panel p-6">
          <h2 className="font-display font-semibold text-lg mb-6 flex items-center gap-2">
            <Users className="h-5 w-5 text-cyan" />
            Role-Based Access Control
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-hairline">
                  <th className="pb-3 font-medium text-ink-muted">Role</th>
                  <th className="pb-3 font-medium text-ink-muted">Permissions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hairline">
                <tr>
                  <td className="py-3 font-medium">Admin</td>
                  <td className="py-3 text-ink-muted font-mono text-xs">all permissions</td>
                </tr>
                <tr>
                  <td className="py-3 font-medium">Data Scientist</td>
                  <td className="py-3 text-ink-muted font-mono text-xs">investigate, model_train, query</td>
                </tr>
                <tr>
                  <td className="py-3 font-medium">Analyst</td>
                  <td className="py-3 text-ink-muted font-mono text-xs">investigate, query</td>
                </tr>
                <tr>
                  <td className="py-3 font-medium">Viewer</td>
                  <td className="py-3 text-ink-muted font-mono text-xs">investigate</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div className="space-y-4">
          <h2 className="font-display font-semibold text-lg mb-2 flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-cyan" />
            Active Security Features
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <SecurityCard title="SQL Injection Defence" desc="16 security tests" icon={Database} />
            <SecurityCard title="Prompt Injection" desc="3-layer defense" icon={Shield} />
            <SecurityCard title="Tenant Isolation" desc="structural workspace scoping" icon={Server} />
            <SecurityCard title="Token Rotation" desc="refresh rotation + reuse detection" icon={Key} />
            <SecurityCard title="Rate Limiting" desc="Redis + in-memory fallback" icon={Activity} />
            <SecurityCard title="Query Sandboxing" desc="read-only, timeout, row limits" icon={Lock} />
          </div>
        </div>
      </div>

      <div className="panel p-6">
        <h2 className="font-display font-semibold text-lg mb-6 flex items-center gap-2">
          <FileSearch className="h-5 w-5 text-cyan" />
          Audit Log
        </h2>
        <div className="py-8">
          <EmptyState title="No events found" body="Sign in to view audit events." />
        </div>
      </div>
    </Shell>
  );
}

function SecurityCard({ title, desc, icon: Icon }: any) {
  return (
    <div className="border border-hairline bg-surface/50 rounded-lg p-4 flex gap-3">
      <Icon className="h-5 w-5 text-cyan shrink-0" />
      <div>
        <h4 className="font-medium text-sm">{title}</h4>
        <p className="text-xs text-ink-muted mt-1">{desc}</p>
      </div>
    </div>
  );
}
