import { useState } from "react";
import { Link } from "react-router-dom";
import { useApp } from "../state/AppState";
import { Card, Chip, Mono, Notice, PageHeader, Spinner } from "../components/ui";
import { ClassTag } from "../components/Charts";
import { formatBytes, formatDuration } from "../lib/format";

export default function Help() {
  const { config } = useApp();
  const [filter, setFilter] = useState("");
  if (!config) return <Spinner />;
  const L = config.limits;
  const f = filter.trim().toLowerCase();

  return (
    <div className="stack-v">
      <PageHeader title="Help and limitations" lead="What FieldLedger does, how to use it, and what it cannot tell you." />
      <Notice title="What a finding means">{config.notices.evidence}</Notice>

      <div className="grid-2">
        <Card title="How to use it">
          <div className="prose">
            <ol>
              <li><Link to="/upload">Upload</Link> CSV files or SQLite databases, and optionally a purpose and retention metadata file. Or load the bundled synthetic data.</li>
              <li>Run a <Link to="/scan">scan</Link>. FieldLedger reads the structure of each file and applies the rules to column names.</li>
              <li>Open <Link to="/findings">Findings</Link>, search and filter, then open a row to see why it matched. Mark it confirmed, needs follow-up, or not personal data.</li>
              <li>Check <Link to="/metadata">Metadata coverage</Link> for flagged columns without a declared purpose or retention period.</li>
              <li>Download the JSON, CSV or Markdown report from the Scan page, and your review marks from Findings.</li>
              <li>Run the <Link to="/evaluation">Evaluation</Link> to see precision and recall on the synthetic benchmark.</li>
            </ol>
            <p className="muted small">Keyboard: Escape closes the finding detail and dialogs.</p>
          </div>
        </Card>
        <Card title="Limits in this installation">
          <dl className="kv">
            <dt>CSV size</dt><dd>{formatBytes(L.max_file_bytes)} · {L.max_rows.toLocaleString()} rows · {L.max_columns.toLocaleString()} columns</dd>
            <dt>SQLite size</dt><dd>{formatBytes(L.max_db_bytes)} · {L.max_tables.toLocaleString()} tables · {L.sqlite_timeout_seconds}s read budget</dd>
            <dt>Workspace</dt><dd>{L.max_files} files · {formatBytes(L.max_workspace_bytes)}</dd>
            <dt>File types</dt><dd>{config.supported_suffixes.join("  ")}</dd>
            <dt>Idle expiry</dt><dd>{formatDuration(L.idle_timeout_seconds)}</dd>
            <dt>Scan time limit</dt><dd>{formatDuration(L.scan_timeout_seconds)}, checked between sources</dd>
          </dl>
        </Card>
      </div>

      <Card title="Limitations">
        <div className="prose">
          <ul>
            <li><b>Names only.</b> {config.notices.heuristic}</li>
            <li><b>Not a legal tool.</b> A finding is not a determination that data is personal data under any law, and a complete declaration is not evidence of compliance. {config.notices.gap}</li>
            <li><b>Rules are configurable and currently <Chip>{config.rules_status}</Chip>.</b> The five classes are the organizers’ taxonomy (<Chip>{config.taxonomy_status}</Chip>). Rules live in <Mono>config/rules.yaml</Mono> and are shown read-only below.</li>
            <li><b>Evaluation is synthetic.</b> Precision and recall describe the team’s own labelled dataset only. They are not a claim about real-world accuracy.</li>
            <li><b>Input formats.</b> {config.csv_notes.join(" ")} SQLite files are opened read-only; only the declared schema and row counts are read.</li>
            <li><b>Review marks are local notes.</b> They live in the temporary workspace, are not part of any report, and disappear when the workspace is cleared or expires.</li>
            <li><b>Temporary workspace.</b> Uploaded files are copied to a temporary folder on this computer and removed on idle expiry, shutdown, or when you clear them. Removal is not secure erasure.</li>
            <li><b>Exports.</b> The CSV inventory prefixes cells that begin with <Mono>= + - @</Mono> with an apostrophe to block spreadsheet formulas; this covers that file only and cannot guarantee how every spreadsheet behaves. The JSON report is the unmodified record. The Markdown summary is plain text.</li>
            <li><b>Cancelling a scan</b> takes effect between files; a file already being read finishes first.</li>
            <li><b>Local use.</b> The server listens on this computer only and has no sign-in. Do not expose it to a network.</li>
          </ul>
        </div>
      </Card>

      <Card title="Classes" aside={<>Taxonomy {config.taxonomy_status}</>}>
        <div className="table-wrap"><table><thead><tr><th>Class</th><th>Identifier</th><th>Covers</th></tr></thead>
          <tbody>{config.classes.map((c) => <tr key={c.id}><td><ClassTag id={c.id} cls={c} /></td><td><Mono>{c.id}</Mono></td><td>{c.description}</td></tr>)}</tbody></table></div>
      </Card>

      <Card title={`Detection rules (${config.rules.length})`} aside="Read-only. Edit config/rules.yaml and restart to change them.">
        <input type="search" placeholder="Filter rules or patterns" aria-label="Filter rules" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ marginBottom: 12 }} />
        {config.classes.map((c) => {
          const rules = config.rules.filter((r) => r.class_id === c.id && (!f || r.id.toLowerCase().includes(f) || r.patterns.some((p) => p.includes(f))));
          if (rules.length === 0) return null;
          return (
            <details className="rule-class" key={c.id} open={Boolean(f)}>
              <summary>{c.label} <span className="muted">· {rules.length} rule{rules.length === 1 ? "" : "s"}</span></summary>
              <div className="stack-v" style={{ marginTop: 10 }}>
                {rules.map((r) => (
                  <div className="rule-box" key={r.id}>
                    <div className="spread"><Mono>{r.id}</Mono><Chip>{r.match === "token" ? "token match" : "exact match"}</Chip></div>
                    <div className="row" style={{ gap: 6 }}>{r.patterns.map((p) => <span className="chip" key={p}><Mono>{p}</Mono></span>)}</div>
                    {r.exclude_patterns.length > 0 && <div className="small muted">Not applied when the name contains: {r.exclude_patterns.join(", ")}</div>}
                  </div>))}
              </div>
            </details>);
        })}
      </Card>

      <Card title="Developers">
        <p className="small">The API is documented in <Mono>docs/api-contract.md</Mono>. The machine-readable schema of this running server is at <a href="/api/openapi.json">/api/openapi.json</a>. FieldLedger {config.version} · API v{config.api_version}.</p>
      </Card>
    </div>
  );
}
