import { useRef, useState } from "react";
import { api } from "../api/client";
import type { UploadResponse } from "../api/types";
import { errText, useApp } from "../state/AppState";
import { Button, Card, Chip, Confirm, Empty, Notice, PageHeader, Spinner } from "../components/ui";
import { formatBytes, formatDuration, plural } from "../lib/format";
import { checkPath, filesFromDrop, isSqlite, relativePathOf, stripCommonRoot, type QueuedFile } from "../lib/upload";

interface Outcome { path: string; ok: boolean; message?: string }

export default function Upload() {
  const { config, workspace, refreshWorkspace, notify, scan } = useApp();
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const metaInput = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [confirmClear, setConfirmClear] = useState(false);
  const [metaError, setMetaError] = useState<string | null>(null);

  if (!config || !workspace) return <Spinner />;
  const lim = config.limits;
  const scanning = scan?.state === "running" || scan?.state === "queued";
  const files = workspace.exists ? workspace.files : [];
  const metadata = workspace.exists ? workspace.metadata : null;
  const total = files.reduce((n, f) => n + f.size_bytes, 0);

  async function send(items: QueuedFile[]) {
    if (items.length === 0) return;
    setBusy(true);
    const out: Outcome[] = [];
    const sendable: QueuedFile[] = [];
    for (const it of items) {
      const bad = checkPath(it.path, config!.supported_suffixes)
        ?? (it.file.size > (isSqlite(it.path) ? lim.max_db_bytes : lim.max_file_bytes) ? "File is larger than the size limit." : null)
        ?? (it.file.size === 0 ? "File is empty." : null);
      if (bad) out.push({ path: it.path, ok: false, message: bad }); else sendable.push(it);
    }
    try {
      for (let i = 0; i < sendable.length; i += lim.max_files_per_request) {
        const batch = sendable.slice(i, i + lim.max_files_per_request);
        const form = new FormData();
        batch.forEach((b) => { form.append("files", b.file, b.file.name); form.append("relative_paths", b.path); });
        const res = await api.upload<UploadResponse>("/api/workspace/files", form);
        res.results.forEach((r) => out.push({ path: r.name, ok: r.accepted, message: r.message }));
      }
    } catch (e) { notify("error", errText(e)); }
    setOutcomes(out);
    const ok = out.filter((o) => o.ok).length;
    if (ok) notify("ok", `${plural(ok, "file")} added to the workspace.`);
    await refreshWorkspace();
    setBusy(false);
  }

  const fromInput = (el: HTMLInputElement | null, strip: boolean) => {
    const list = Array.from(el?.files ?? []).map((file) => ({ file, path: relativePathOf(file) }));
    if (el) el.value = "";
    void send(strip ? stripCommonRoot(list) : list);
  };

  async function uploadMeta(file: File | undefined) {
    if (!file) return;
    setMetaError(null);
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const r = await api.upload<{ entries: number; origin: string }>("/api/workspace/metadata", form);
      notify("ok", `Metadata added: ${plural(r.entries, "entry", "entries")}.`);
      await refreshWorkspace();
    } catch (e) { setMetaError(errText(e)); }
    if (metaInput.current) metaInput.current.value = "";
  }

  async function removeFile(key: string) {
    try { await api.del(`/api/workspace/files?source_key=${encodeURIComponent(key)}`); await refreshWorkspace(); }
    catch (e) { notify("error", errText(e)); }
  }
  async function removeMeta() {
    try { await api.del("/api/workspace/metadata"); await refreshWorkspace(); } catch (e) { notify("error", errText(e)); }
  }
  async function loadBundled() {
    setBusy(true);
    try { await api.post("/api/workspace/bundled"); notify("ok", "Bundled synthetic data loaded."); await refreshWorkspace(); }
    catch (e) { notify("error", errText(e)); }
    setBusy(false);
  }
  async function clearAll() {
    setConfirmClear(false);
    try { await api.del("/api/workspace"); setOutcomes([]); await refreshWorkspace(); notify("ok", "Workspace cleared."); }
    catch (e) { notify("error", errText(e)); }
  }

  const rejected = outcomes.filter((o) => !o.ok);
  return (
    <div className="stack-v">
      <PageHeader title="Upload data" lead="Add the CSV files and SQLite databases to inspect, and optionally a purpose and retention metadata file. Files stay on this computer in a temporary workspace." />
      {scanning && <Notice kind="warn" title="A scan is running">Uploads and removals are paused until it finishes or is cancelled.</Notice>}
      <div className="grid-2">
        <div className="stack-v">
          <Card title="Data files">
            <div
              className={`drop${over ? " over" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setOver(true); }}
              onDragLeave={() => setOver(false)}
              onDrop={async (e) => { e.preventDefault(); setOver(false); if (!scanning) void send(await filesFromDrop(e.dataTransfer)); }}
            >
              <strong>Drop files or a folder here</strong>
              <p className="small">{config.supported_suffixes.join("  ")} · up to {formatBytes(lim.max_file_bytes)} per CSV and {formatBytes(lim.max_db_bytes)} per database</p>
              <div className="row">
                <Button variant="primary" disabled={busy || scanning} onClick={() => fileInput.current?.click()}>Choose files</Button>
                <Button disabled={busy || scanning} onClick={() => folderInput.current?.click()}>Choose folder</Button>
                {config.benchmark_available && files.length === 0 && !metadata && <Button disabled={busy || scanning} onClick={loadBundled}>Use bundled synthetic data</Button>}
              </div>
              <input ref={fileInput} type="file" multiple hidden accept={config.supported_suffixes.join(",")} onChange={(e) => fromInput(e.target, false)} />
              <input ref={folderInput} type="file" hidden multiple onChange={(e) => fromInput(e.target, true)} {...({ webkitdirectory: "" } as object)} />
            </div>
            {busy && <Spinner label="Uploading" />}
            {rejected.length > 0 && (
              <Notice kind="warn" title={`${plural(rejected.length, "file")} not added`}>
                <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{rejected.slice(0, 12).map((r, i) => <li key={i}><code className="mono">{r.path}</code>: {r.message}</li>)}</ul>
                {rejected.length > 12 && <span>…and {rejected.length - 12} more.</span>}
              </Notice>
            )}
            <ul className="muted small" style={{ margin: "12px 0 0", paddingLeft: 18 }}>
              {config.csv_notes.map((n) => <li key={n}>{n}</li>)}
              <li>Folder structure is kept: a file at <code className="mono">crm/customers.csv</code> is identified by that path.</li>
            </ul>
          </Card>

          <Card title={`Workspace files (${files.length})`} aside={files.length > 0 ? `${formatBytes(total)} of ${formatBytes(lim.max_workspace_bytes)}` : undefined}>
            {files.length === 0 ? <Empty title="No files yet">Files you add appear here.</Empty> : (
              <div className="table-wrap"><table>
                <thead><tr><th>File</th><th>Type</th><th className="num">Size</th><th /></tr></thead>
                <tbody>{files.map((f) => (
                  <tr key={f.source_key}>
                    <td><code className="mono">{f.source_key}</code></td>
                    <td><Chip>{f.type === "csv" ? "CSV" : "SQLite"}</Chip></td>
                    <td className="num">{formatBytes(f.size_bytes)}</td>
                    <td className="num"><Button small variant="ghost" disabled={scanning} onClick={() => removeFile(f.source_key)} aria-label={`Remove ${f.source_key}`}>Remove</Button></td>
                  </tr>))}</tbody>
              </table></div>
            )}
          </Card>
        </div>

        <div className="stack-v">
          <Card title="Purpose and retention metadata" aside="Optional">
            <p className="small muted">A YAML file that declares, per column, the purpose and retention of the data. FieldLedger reports where a declaration is missing; it does not judge the declarations. Entries must use the same file paths as the uploaded files.</p>
            {metadata ? (
              <div className="spread">
                <span><Chip tone="teal">{plural(metadata.entries, "entry", "entries")}</Chip> <span className="muted small">origin: {metadata.origin}</span></span>
                <Button small disabled={scanning} onClick={removeMeta}>Remove</Button>
              </div>
            ) : (
              <Button disabled={scanning} onClick={() => metaInput.current?.click()}>Choose metadata file</Button>
            )}
            <input ref={metaInput} type="file" hidden accept=".yaml,.yml" onChange={(e) => uploadMeta(e.target.files?.[0])} />
            {metaError && <div style={{ marginTop: 10 }}><Notice kind="error" title="Metadata file rejected">{metaError}</Notice></div>}
            <p className="small muted" style={{ marginTop: 12 }}>Accepted origins: {config.metadata_origins.join(", ")}. Maximum {formatBytes(lim.max_metadata_bytes)}.</p>
          </Card>
          <Card title="Workspace">
            <p className="small muted">The workspace is a temporary folder on this computer. It is removed when it has been idle for {formatDuration(lim.idle_timeout_seconds)}, when the server stops, or when you clear it. Clearing removes the files from this workspace; it is not secure erasure, and the operating system may keep recoverable copies.</p>
            <Button variant="danger" disabled={!workspace.exists || scanning} onClick={() => setConfirmClear(true)}>Clear workspace</Button>
          </Card>
        </div>
      </div>
      <Confirm open={confirmClear} title="Clear the workspace?" confirmLabel="Clear workspace" danger onConfirm={clearAll} onCancel={() => setConfirmClear(false)}>
        This removes {plural(files.length, "file")}{metadata ? ", the metadata file" : ""}, the scan results, exports and {plural(workspace.exists ? workspace.review_count : 0, "review mark")} from this workspace. It does not securely erase the disk.
      </Confirm>
    </div>
  );
}
