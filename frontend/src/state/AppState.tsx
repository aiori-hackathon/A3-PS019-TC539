import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "../api/client";
import type { AppConfig, ReportFormat, ScanStatus, ScanSummary, WorkspaceState } from "../api/types";

interface Toast { id: number; kind: "ok" | "error"; text: string }

interface Ctx {
  config: AppConfig | null;
  configError: string | null;
  workspace: WorkspaceState | null;
  scan: ScanStatus | null;
  summary: ScanSummary | null;
  refreshWorkspace: () => Promise<WorkspaceState | null>;
  refreshSummary: () => Promise<void>;
  startScan: (opts: { include_declarations: boolean; formats: ReportFormat[] }) => Promise<void>;
  cancelScan: () => Promise<void>;
  notify: (kind: "ok" | "error", text: string) => void;
  resetLocal: () => void;
  scanReady: boolean;
}

const AppCtx = createContext<Ctx | null>(null);
export const useApp = (): Ctx => {
  const v = useContext(AppCtx);
  if (!v) throw new Error("AppProvider missing");
  return v;
};

export function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceState | null>(null);
  const [scan, setScan] = useState<ScanStatus | null>(null);
  const [summary, setSummary] = useState<ScanSummary | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastId = useRef(0);

  const notify = useCallback((kind: "ok" | "error", text: string) => {
    const id = ++toastId.current;
    setToasts((t) => [...t, { id, kind, text }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 8000 : 3500);
  }, []);

  const loadScan = useCallback(async (scanId: string | null) => {
    if (!scanId) { setScan(null); setSummary(null); return; }
    try {
      const s = await api.get<ScanStatus>(`/api/scans/${scanId}`);
      setScan(s);
      if (s.state === "succeeded") setSummary(await api.get<ScanSummary>(`/api/scans/${scanId}/summary`));
      else setSummary(null);
    } catch (e) {
      if (e instanceof ApiError && (e.code === "not_found" || e.code === "no_workspace")) { setScan(null); setSummary(null); }
    }
  }, []);

  const refreshWorkspace = useCallback(async () => {
    try {
      const w = await api.get<WorkspaceState>("/api/workspace");
      setWorkspace(w);
      if (!w.exists) { setScan(null); setSummary(null); return w; }
      setScan((cur) => {
        if (w.latest_scan_id && cur?.scan_id !== w.latest_scan_id) void loadScan(w.latest_scan_id);
        else if (!w.latest_scan_id) { setSummary(null); return null; }
        else if (cur && cur.workspace_revision !== w.revision && !cur.stale) return { ...cur, stale: true };
        return cur;
      });
      return w;
    } catch (e) {
      notify("error", errText(e));
      return null;
    }
  }, [loadScan, notify]);

  const refreshSummary = useCallback(async () => {
    if (scan?.state === "succeeded") setSummary(await api.get<ScanSummary>(`/api/scans/${scan.scan_id}/summary`));
  }, [scan]);

  useEffect(() => {
    api.get<AppConfig>("/api/config").then(setConfig).catch((e) => setConfigError(errText(e)));
    void refreshWorkspace();
    const onFocus = () => void refreshWorkspace();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshWorkspace]);

  // Poll only while a scan is queued or running.
  const active = scan?.state === "queued" || scan?.state === "running";
  const scanId = scan?.scan_id;
  useEffect(() => {
    if (!active || !scanId) return;
    const t = window.setInterval(async () => {
      try {
        const s = await api.get<ScanStatus>(`/api/scans/${scanId}`);
        setScan(s);
        if (s.state === "succeeded") {
          setSummary(await api.get<ScanSummary>(`/api/scans/${scanId}/summary`));
          notify("ok", "Scan finished.");
        } else if (s.state === "failed") notify("error", s.error ?? "The scan failed.");
        else if (s.state === "cancelled") notify("ok", "Scan cancelled.");
      } catch (e) { notify("error", errText(e)); }
    }, 600);
    return () => window.clearInterval(t);
  }, [active, scanId, notify]);

  const startScan = useCallback(async (opts: { include_declarations: boolean; formats: ReportFormat[] }) => {
    try {
      const { scan_id } = await api.post<{ scan_id: string }>("/api/scans", opts);
      setSummary(null);
      setScan(await api.get<ScanStatus>(`/api/scans/${scan_id}`));
    } catch (e) { notify("error", errText(e)); }
  }, [notify]);

  const cancelScan = useCallback(async () => {
    if (!scan) return;
    try { setScan(await api.post<ScanStatus>(`/api/scans/${scan.scan_id}/cancel`)); }
    catch (e) { notify("error", errText(e)); }
  }, [scan, notify]);

  const resetLocal = useCallback(() => { setScan(null); setSummary(null); }, []);

  const value = useMemo<Ctx>(() => ({
    config, configError, workspace, scan, summary, refreshWorkspace, refreshSummary, startScan, cancelScan,
    notify, resetLocal, scanReady: scan?.state === "succeeded" && summary !== null,
  }), [config, configError, workspace, scan, summary, refreshWorkspace, refreshSummary, startScan, cancelScan, notify, resetLocal]);

  return (
    <AppCtx.Provider value={value}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className={`toast toast-${t.kind}`}>{t.text}</div>)}
      </div>
    </AppCtx.Provider>
  );
}
