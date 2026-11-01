import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SourceInfo } from "../api/types";
import { useApp } from "../state/AppState";
import { useAsync } from "../lib/useAsync";
import { Card, Chip, ErrorBox, LinkButton, Notice, NeedsScan, PageHeader, Spinner } from "../components/ui";
import { formatBytes, plural } from "../lib/format";

export default function Sources() {
  const { workspace, scan, scanReady } = useApp();
  const { data, error, loading, reload } = useAsync(
    () => api.get<{ sources: SourceInfo[] }>(`/api/scans/${scan!.scan_id}/sources`), [scan?.scan_id, scan?.state], scanReady);
  const [selected, setSelected] = useState<string | null>(null);
  const [tableName, setTableName] = useState<string | null>(null);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [filter, setFilter] = useState("");

  const sources = data?.sources ?? [];
  const current = sources.find((s) => s.source_key === selected) ?? sources[0];
  const table = current?.tables.find((t) => t.name === tableName) ?? current?.tables[0];
  const columns = useMemo(() => (table?.columns ?? []).filter((c) =>
    (!flaggedOnly || c.finding_count > 0) && c.name.toLowerCase().includes(filter.toLowerCase())), [table, flaggedOnly, filter]);

  const lead = "The structure of each scanned file: tables, columns and their types. Only names, types and counts are shown. Cell values are never displayed.";
  if (!scanReady) {
    const files = workspace?.exists ? workspace.files : [];
    return (
      <div className="stack-v">
        <PageHeader title="Source explorer" lead={lead} />
        {files.length > 0 && (
          <Card title="Files waiting to be scanned">
            <div className="table-wrap"><table><thead><tr><th>File</th><th>Type</th><th className="num">Size</th></tr></thead>
              <tbody>{files.map((f) => <tr key={f.source_key}><td><code className="mono">{f.source_key}</code></td><td>{f.type === "csv" ? "CSV" : "SQLite"}</td><td className="num">{formatBytes(f.size_bytes)}</td></tr>)}</tbody></table></div>
          </Card>
        )}
        {scan && (scan.state === "running" || scan.state === "queued") ? <Spinner label="Scan running" /> : <NeedsScan what="Tables and columns" />}
      </div>
    );
  }
  if (loading && !data) return <Spinner />;
  if (error) return <ErrorBox message={error} onRetry={reload} />;

  return (
    <div className="stack-v">
      <PageHeader title="Source explorer" lead={lead} actions={<LinkButton to="/findings">Open findings</LinkButton>} />
      {scan && scan.source_errors.length > 0 && (
        <Notice kind="error" title={`${plural(scan.source_errors.length, "source")} could not be read`}>
          {scan.source_errors.map((e) => e.source_key).join(", ")}. See the Scan page for the reasons.
        </Notice>
      )}
      <div className="grid-2" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,2.4fr)" }}>
        <Card title={`Sources (${sources.length})`}>
          <div className="table-wrap"><table>
            <thead><tr><th>Source</th><th className="num">Findings</th></tr></thead>
            <tbody>{sources.map((s) => (
              <tr key={s.source_key} className={`clickable${s.source_key === current?.source_key ? " selected" : ""}`} onClick={() => { setSelected(s.source_key); setTableName(null); }}>
                <td><button className="link-btn" style={{ textDecoration: "none", color: "inherit", textAlign: "left" }} onClick={() => { setSelected(s.source_key); setTableName(null); }}><code className="mono">{s.source_key}</code></button>
                  <div className="cell-sub">{s.source_type === "csv" ? "CSV" : "SQLite"} · {plural(s.tables.length, "table")}{s.warnings.length > 0 && <> · <Chip tone="amber">delimiter?</Chip></>}</div></td>
                <td className="num">{s.finding_count}</td>
              </tr>))}</tbody>
          </table></div>
        </Card>

        {current && table && (
          <Card title={<code className="mono">{current.source_key}</code>} aside={current.type_basis === "declared_schema" ? "Types come from the declared schema; no values were read" : "Types observed from file contents"}>
            {current.warnings.includes("possible_delimiter_mismatch") && (
              <div style={{ marginBottom: 12 }}><Notice kind="warn" title="Possible delimiter mismatch">This CSV was read as a single column whose name contains <code className="mono">;</code>, <code className="mono">|</code> or a tab. FieldLedger only reads comma-separated files, so detection on this file is unreliable. Re-export it with commas.</Notice></div>
            )}
            {current.tables.length > 1 && (
              <div className="row" style={{ marginBottom: 12 }}>
                {current.tables.map((t) => (
                  <button key={t.name} className={`fchip${t.name === table.name ? " on" : ""}`} onClick={() => setTableName(t.name)}>{t.name}<span className="fchip-n">{t.columns.length}</span></button>))}
              </div>
            )}
            <div className="spread" style={{ marginBottom: 12 }}>
              <span className="muted small">Table <code className="mono">{table.name}</code> · {table.row_count === null ? "row count unknown" : plural(table.row_count, "row")} · {plural(table.columns.length, "column")}</span>
              <div className="row">
                <input type="search" placeholder="Filter columns" aria-label="Filter columns" value={filter} onChange={(e) => setFilter(e.target.value)} />
                <button className={`fchip${flaggedOnly ? " on" : ""}`} aria-pressed={flaggedOnly} onClick={() => setFlaggedOnly(!flaggedOnly)}>Flagged only</button>
              </div>
            </div>
            <div className="table-wrap"><table>
              <thead><tr><th className="num">#</th><th>Column</th><th>Type</th><th className="num">Non-null</th><th className="num">Null</th><th>Flagged</th></tr></thead>
              <tbody>{columns.map((c) => (
                <tr key={c.position}>
                  <td className="num muted">{c.position}</td>
                  <td><code className="mono">{c.name}</code></td>
                  <td>{c.inferred_type}{c.declared_type ? <span className="cell-sub"> · declared {c.declared_type}</span> : null}</td>
                  <td className="num">{c.non_null_count ?? "-"}</td><td className="num">{c.null_count ?? "-"}</td>
                  <td>{c.finding_count > 0 ? <Link to={`/findings?q=${encodeURIComponent(c.name)}&source=${encodeURIComponent(current.source_key)}`}><Chip tone="teal">{plural(c.finding_count, "finding")}</Chip></Link> : <span className="muted">-</span>}</td>
                </tr>))}
                {columns.length === 0 && <tr><td colSpan={6} className="muted">No columns match.</td></tr>}</tbody>
            </table></div>
            <p className="muted small" style={{ marginTop: 10 }}>The type is a broad storage type, not a personal-data class. “Flagged” means a rule matched the column name.</p>
          </Card>
        )}
      </div>
    </div>
  );
}
