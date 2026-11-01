import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { MetadataReport, MetadataStatus } from "../api/types";
import { useApp } from "../state/AppState";
import { useAsync } from "../lib/useAsync";
import { Card, Chip, Empty, ErrorBox, FilterChip, LinkButton, Mono, NeedsScan, Notice, PageHeader, Spinner, StatusChip } from "../components/ui";
import { Legend, STATUS_COLORS, STATUS_ORDER, StackedBar } from "../components/Charts";
import { STATUS_LABEL, plural } from "../lib/format";

const STEP = 50;

export default function Metadata() {
  const { config, scan, scanReady } = useApp();
  const { data, error, loading, reload } = useAsync(() => api.get<MetadataReport>(`/api/scans/${scan!.scan_id}/metadata`), [scan?.scan_id], scanReady);
  const [sel, setSel] = useState<MetadataStatus[]>(["missing_purpose", "missing_retention", "missing_both", "no_entry"]);
  const [shown, setShown] = useState(STEP);
  const [orphanShown, setOrphanShown] = useState(STEP);

  const rows = useMemo(() => (data?.available ? data.linked_fields.filter((r) => sel.includes(r.status)) : []), [data, sel]);
  const lead = "Which flagged columns have a declared purpose and retention period, and which do not. A gap is something to review in your metadata, not a finding of non-compliance.";

  if (!scanReady) return <div className="stack-v"><PageHeader title="Metadata coverage" lead={lead} /><NeedsScan what="Metadata coverage" /></div>;
  if (loading && !data) return <Spinner />;
  if (error) return <ErrorBox message={error} onRetry={reload} />;
  if (!data || !config) return null;
  if (!data.available) {
    return (
      <div className="stack-v">
        <PageHeader title="Metadata coverage" lead={lead} />
        <Empty title="No metadata was supplied for this scan" action={<LinkButton to="/upload" variant="primary">Add a metadata file</LinkButton>}>
          Upload a purpose and retention YAML file, then run the scan again to see coverage.
        </Empty>
      </div>
    );
  }
  const s = data.summary;
  const toggle = (st: MetadataStatus) => { setSel((c) => (c.includes(st) ? c.filter((x) => x !== st) : [...c, st])); setShown(STEP); };
  const cls = (id: string) => config.classes.find((c) => c.id === id)?.label ?? id;

  return (
    <div className="stack-v">
      <PageHeader title="Metadata coverage" lead={lead} />
      <Notice>{data.review_gap_notice}</Notice>

      <Card title="Declaration status of flagged columns" aside={<>Origin: <Chip>{data.metadata_origin}</Chip></>}>
        <StackedBar total={s.flagged_columns} label="Declaration status of flagged columns" parts={STATUS_ORDER.map((k) => ({ key: k, label: STATUS_LABEL[k], value: s.by_status[k], color: STATUS_COLORS[k] }))} />
        <Legend items={STATUS_ORDER.map((k) => ({ key: k, label: STATUS_LABEL[k], value: s.by_status[k], color: STATUS_COLORS[k] }))} />
        <p className="muted small" style={{ marginTop: 12 }}>
          {plural(s.flagged_columns, "flagged column")} (a column that fits two classes counts once here) against {plural(s.metadata_entries, "metadata entry", "metadata entries")}.
          {data.declarations_included ? " Purpose and retention text is shown because declarations were included in this scan." : " Declaration text is hidden. Enable it in the scan settings if you need to read it here."}
        </p>
      </Card>

      <Card title={`Flagged columns (${rows.length})`}>
        <div className="toolbar-row" style={{ marginBottom: 12 }}>
          {STATUS_ORDER.map((k) => <FilterChip key={k} active={sel.includes(k)} onClick={() => toggle(k)} count={s.by_status[k]}>{STATUS_LABEL[k]}</FilterChip>)}
        </div>
        <div className="table-wrap"><table>
          <thead><tr><th>Column</th><th>Class</th><th>Status</th><th>Purpose</th><th>Retention</th><th /></tr></thead>
          <tbody>
            {rows.slice(0, shown).map((r) => (
              <tr key={`${r.source_key}|${r.table}|${r.column}`}>
                <td><Mono>{r.column}</Mono><div className="cell-sub"><code className="mono">{r.source_key}</code>{r.table ? ` · ${r.table}` : ""}</div></td>
                <td>{r.class_ids.map(cls).join(", ")}{r.ambiguous && <div className="cell-sub">ambiguous</div>}</td>
                <td><StatusChip status={r.status} /></td>
                <td>{data.declarations_included ? (r.purpose ?? <span className="muted">not declared</span>) : (r.has_purpose ? "declared" : <span className="muted">missing</span>)}</td>
                <td>{data.declarations_included ? (r.retention ?? <span className="muted">not declared</span>) : (r.has_retention ? "declared" : <span className="muted">missing</span>)}</td>
                <td><Link to={`/findings?q=${encodeURIComponent(r.column)}&source=${encodeURIComponent(r.source_key)}`}>Findings</Link></td>
              </tr>))}
            {rows.length === 0 && <tr><td colSpan={6} className="muted">No flagged columns have the selected status.</td></tr>}
          </tbody>
        </table></div>
        {rows.length > shown && <div style={{ marginTop: 12 }}><button className="btn btn-secondary btn-sm" onClick={() => setShown(shown + STEP)}>Show {Math.min(STEP, rows.length - shown)} more</button> <span className="muted small">{shown} of {rows.length} shown</span></div>}
      </Card>

      <div className="grid-eq">
        <Card title={`Declared but not flagged (${data.orphan_entries.length})`}>
          <p className="small muted">Metadata entries for columns that exist in the scan but that no rule matched. They are not errors. They can show columns the rules miss, such as opaque names.</p>
          <div className="table-wrap"><table><thead><tr><th>Column</th><th>Purpose</th><th>Retention</th></tr></thead>
            <tbody>{data.orphan_entries.slice(0, orphanShown).map((e, i) => (
              <tr key={i}><td><Mono>{e.column}</Mono><div className="cell-sub"><code className="mono">{e.source_key}</code></div></td><td>{e.has_purpose ? "declared" : <span className="muted">missing</span>}</td><td>{e.has_retention ? "declared" : <span className="muted">missing</span>}</td></tr>))}
              {data.orphan_entries.length === 0 && <tr><td colSpan={3} className="muted">None.</td></tr>}</tbody></table></div>
          {data.orphan_entries.length > orphanShown && <button className="btn btn-secondary btn-sm" style={{ marginTop: 10 }} onClick={() => setOrphanShown(orphanShown + STEP)}>Show more</button>}
        </Card>
        <Card title={`Entries that match nothing (${data.unmatched_entries.length})`}>
          <p className="small muted">Metadata entries whose file, table or column is not in this scan. Check the file path, table name and spelling.</p>
          <div className="table-wrap"><table><thead><tr><th>Entry</th><th>Why</th></tr></thead>
            <tbody>{data.unmatched_entries.slice(0, 100).map((e, i) => (
              <tr key={i}><td><Mono>{e.column}</Mono><div className="cell-sub"><code className="mono">{e.source_key}</code> · {e.table}</div></td>
                <td>{({ source_not_scanned: "File not scanned", table_not_found: "Table not found", column_not_found: "Column not found" } as Record<string, string>)[e.reason] ?? e.reason}{e.case_variant_exists && <div className="cell-sub">A column differing only by letter case exists.</div>}</td></tr>))}
              {data.unmatched_entries.length === 0 && <tr><td colSpan={2} className="muted">None. Every entry matched a scanned column.</td></tr>}</tbody></table></div>
        </Card>
      </div>
    </div>
  );
}
