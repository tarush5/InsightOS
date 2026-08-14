"use client";

import { useEffect, useState } from "react";
import { Shell, SignInRequired, PageHeading, EmptyState } from "@/components/Shell";
import { useSession } from "@/lib/session";
import { api } from "@/lib/api";
import { HistoryRow } from "@/lib/types";
import Link from "next/link";
import { Download, FileText, ArrowRight, FileJson, Table } from "lucide-react";

export default function ReportsPage() {
  const { status } = useSession();
  const [reports, setReports] = useState<HistoryRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedRef, setSelectedRef] = useState<string | null>(null);

  useEffect(() => {
    if (status !== "signed-in") return;
    api.history(50, 0)
      .then((res) => {
        const valid = res.investigations.filter((inv) => inv.verdict === "answered");
        setReports(valid);
        if (valid.length > 0) {
          setSelectedRef(valid[0]?.reference ?? null);
        }
      })
      .catch((err) => console.error(err))
      .finally(() => setLoading(false));
  }, [status]);

  if (status === "unknown") return null;
  if (status !== "signed-in") {
    return (
      <Shell>
        <SignInRequired what="view reports" />
      </Shell>
    );
  }

  const handleExportJson = async () => {
    if (!selectedRef) return;
    try {
      const data = await api.investigation(selectedRef);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      downloadBlob(blob, `investigation-${selectedRef}.json`);
    } catch (e) {
      console.error(e);
    }
  };

  const handleExportCsv = async () => {
    if (!selectedRef) return;
    try {
      const data = await api.investigation(selectedRef);
      let csv = "key,value\n";
      if (data.result && typeof data.result === 'object') {
        Object.entries(data.result).forEach(([k, v]) => {
          csv += `"${k}","${String(v).replace(/"/g, '""')}"\n`;
        });
      }
      const blob = new Blob([csv], { type: "text/csv" });
      downloadBlob(blob, `investigation-${selectedRef}.csv`);
    } catch (e) {
      console.error(e);
    }
  };

  const handleExportMd = async () => {
    if (!selectedRef) return;
    try {
      const md = await api.investigationExport(selectedRef, 'markdown');
      const blob = new Blob([md], { type: "text/markdown" });
      downloadBlob(blob, `investigation-${selectedRef}.md`);
    } catch (e) {
      console.error(e);
    }
  };

  const downloadBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <Shell>
      <PageHeading eyebrow="AI-Generated Executive Intelligence" title="Reports">
        Review and export completed investigations.
      </PageHeading>

      <div className="grid grid-cols-1 xl:grid-cols-4 gap-8">
        <div className="xl:col-span-3 space-y-4">
          {loading ? (
            <div className="animate-pulseSoft panel h-64 flex items-center justify-center text-ink-muted">Loading reports...</div>
          ) : reports.length === 0 ? (
            <EmptyState title="No reports available" body="No investigations completed yet. Start an investigation to generate reports." />
          ) : (
            reports.map((report) => (
              <div 
                key={report.reference} 
                onClick={() => setSelectedRef(report.reference)}
                className={`panel p-5 flex flex-col sm:flex-row gap-4 justify-between items-start sm:items-center cursor-pointer transition-colors ${selectedRef === report.reference ? "border-cyan" : "hover:border-ink-faint"}`}
              >
                <div>
                  <div className="flex items-center gap-3 mb-2">
                    <span className="font-mono text-xs text-ink-muted">{report.reference}</span>
                    {report.confidence != null && (
                      <span className={`px-2 py-0.5 rounded text-[10px] font-mono ${report.confidence >= 0.7 ? "bg-ok/10 text-ok" : report.confidence >= 0.4 ? "bg-warn/10 text-warn" : "bg-crit/10 text-crit"}`}>
                        {Math.round(report.confidence * 100)}% CONFIDENCE
                      </span>
                    )}
                  </div>
                  <h3 className="font-display font-semibold text-lg">{report.headline || report.question}</h3>
                  <p className="text-sm text-ink-muted mt-1 line-clamp-1">{report.question}</p>
                </div>
                <Link
                  href={`/investigate?ref=${report.reference}`}
                  className="shrink-0 flex items-center gap-2 px-4 py-2 rounded-lg bg-elevated border border-hairline hover:bg-hairline transition-colors text-sm font-medium"
                  onClick={(e) => e.stopPropagation()}
                >
                  View Full Report <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
            ))
          )}
        </div>

        <div className="xl:col-span-1 space-y-4">
          <div className="panel p-5 sticky top-6">
            <h3 className="label-mono text-xs mb-4">Export Options</h3>
            <p className="text-sm text-ink-muted mb-6">
              Export your findings to share with stakeholders or integrate into downstream tools.
            </p>
            <div className="space-y-3">
              <ExportBtn icon={FileText} label="PDF Report" disabled={!selectedRef} onClick={() => alert('PDF export coming soon')} />
              <ExportBtn icon={Download} label="Markdown" disabled={!selectedRef} onClick={handleExportMd} />
              <ExportBtn icon={Table} label="CSV Data" disabled={!selectedRef} onClick={handleExportCsv} />
              <ExportBtn icon={FileJson} label="JSON Payload" disabled={!selectedRef} onClick={handleExportJson} />
            </div>
            {selectedRef && (
              <div className="mt-6 border-t border-hairline pt-6">
                <Link
                  href={`/investigate?ref=${selectedRef}`}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-cyan text-black font-medium transition-opacity hover:opacity-90 text-sm"
                >
                  View Full Report <ArrowRight className="h-4 w-4" />
                </Link>
              </div>
            )}
          </div>
        </div>
      </div>
    </Shell>
  );
}

function ExportBtn({ icon: Icon, label, disabled, onClick }: any) {
  return (
    <button onClick={onClick} disabled={disabled} className="w-full flex items-center gap-3 p-3 rounded-lg border border-hairline hover:bg-elevated transition-colors text-sm disabled:opacity-50 disabled:cursor-not-allowed">
      <Icon className="h-4 w-4 text-cyan" />
      <span>{label}</span>
    </button>
  );
}
