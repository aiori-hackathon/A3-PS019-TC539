import { useState } from "react";
import { Link } from "react-router-dom";
import { exportUrl } from "../api/client";
import type { ReportFormat } from "../api/types";
import { useApp } from "../state/AppState";
import { Button, Card, Chip, Confirm, Empty, LinkButton, Notice, PageHeader, Spinner } from "../components/ui";
import { SOURCE_ERROR_LABEL, STAGE_LABEL, formatDuration, formatTime, plural } from "../lib/format";

const FORMAT_INFO: Record<ReportFormat, { name: string; file: string }> = {
  json: { name: "JSON report", file: "fieldledger_report.json" },
  csv: { name: "CSV inventory", file: "fieldledger_inventory.csv" },
  md: { name: "Markdown summary", file: "fieldledger_summary.md" },
};

export default function Scan() {
  const { config, workspace, scan, summary, startScan, cancelScan } = useApp();
  const [declarations, setDeclarations] = useState(false);
  const [formats, setFormats] = useState<ReportFormat[]>(["json", "csv", "md"]);
  const [warn, setWarn] = useState(false);
  if (!config || !workspace) return <Spinner />;

  const files = workspace.exists ? workspace.files.length : 0;
  const hasMeta = workspace.exists && workspace.metadata !== null;
  const active = scan?.state === "running" || scan?.state === "queued";
  const canStart = files > 0 && !active && formats.length > 0;
  const toggle = (f: ReportFormat) => setFormats((cur) => cur.includes(f) ? cur.filter((x) => x !== f) : [...cur, f]);
  const run = () => void startScan({ include_declarations: declarations && hasMeta, formats });

  return (
    <div className="stack-v">
      <PageHeader title="Scan" lead="Read the structure of every file in the workspace, apply the detection rules to column names, and link findings to your metadata. Cell values are never read by the rules." />
      {files === 0 && !active && (
        <Empty title="Nothing to scan" action={<LinkButton to="/upload" variant="primary">Add files</LinkButton>}>Add at least one CSV file or SQLite database first.</Empty>
      )}
      {files > 0 && (
        <div className="grid-2">
          <Card title="Scan settings" aside={`${plural(files, "file")} in workspace`}>
            <div className="stack-v">
              <div>
                <div className="sec-title">Report formats</div>
                <div className="stack-v" style={{ gap: 10 }}>
                  {config.report_formats.map((f) => (
                    <label className="check" key={f}>
                      <input type="checkbox" checked={formats.includes(f)} disabled={active} onChange={() => toggle(f)} />
                      <span>{FORMAT_INFO[f].name}<small className="mono">{FORMAT_INFO[f].file}</small></span>
                    </label>
                  ))}
                </div>
              </div>
              <div>
                <div className="sec-title">Declarations</div>
                <label className="check">
                  <input type="checkbox" checked={declarations && hasMeta} disabled={!hasMeta || active} onChange={(e) => e.target.checked ? setWarn(true) : setDeclarations(false)} />
                  <span>Include purpose and retention text in results and exports
                    <small>{hasMeta ? "Off by default. When on, the text you wrote in the metadata file appears in the findings detail, the metadata page and the exports." : "Upload a metadata file to enable this."}</small>
                  </span>
                </label>
              </div>
              <div className="row">
                <Button variant="primary" disabled={!canStart} onClick={run}>{scan && !active ? "Run scan again" : "Run scan"}</Button>
                {formats.length === 0 && <span className="small queue-bad">Choose at least one format.</span>}
              </div>
            </div>
          </Card>

          <Card title="Status">
            {!scan && <p className="muted">No scan has been run in this workspace.</p>}
            {scan && (
              <div className="stack-v" style={{ gap: 12 }}>
                <div className="row">
                  <Chip tone={scan.state === "succeeded" ? "teal" : scan.state === "failed" ? "red" : scan.state === "cancelled" ? "amber" : "navy"}>{scan.state}</Chip>
                  {scan.stale && <Chip tone="amber">workspace changed since this scan</Chip>}
                </div>
                {active && (
                  <>
                    <div className="indeterminate" role="progressbar" aria-label="Scan in progress" />
                    <div>{scan.stage ? STAGE_LABEL[scan.stage] : "Starting"}{scan.sources_total !== null && <> · <b>{scan.sources_done} of {scan.sources_total}</b> sources</>}</div>
                    <div className="row"><Button small onClick={cancelScan}>Cancel scan</Button><span className="small muted">Cancelling takes effect between sources. A source already being read finishes first.</span></div>
                  </>
                )}
                <dl className="kv">
                  <dt>Started</dt><dd>{formatTime(scan.started_at)}</dd>
                  <dt>Finished</dt><dd>{formatTime(scan.finished_at)}</dd>
                  {scan.sources_total !== null && <><dt>Sources</dt><dd>{scan.sources_done} of {scan.sources_total} processed</dd></>}
                  {summary && <><dt>Findings</dt><dd>{summary.total_findings} in {plural(summary.sources_scanned, "source")}</dd></>}
                </dl>
                {scan.state === "failed" && <Notice kind="error" title="The scan failed">{scan.error ?? "Unknown error."}</Notice>}
                {scan.state === "succeeded" && (
                  <div className="row">
                    <LinkButton to="/findings" variant="primary">Review findings</LinkButton>
                    <LinkButton to="/">Overview</LinkButton>
                  </div>
                )}
              </div>
            )}
          </Card>
        </div>
      )}

      {scan && scan.source_errors.length > 0 && (
        <Card title={`${plural(scan.source_errors.length, "source")} could not be scanned`}>
          <Notice kind="error">These files were skipped and are not included in any count. The rest of the scan completed.</Notice>
          <div className="table-wrap" style={{ marginTop: 12 }}><table>
            <thead><tr><th>File</th><th>Problem</th><th>Detail</th></tr></thead>
            <tbody>{scan.source_errors.map((e, i) => (
              <tr key={i}><td><code className="mono">{e.source_key}</code></td><td>{SOURCE_ERROR_LABEL[e.code] ?? e.code}</td><td>{e.message}</td></tr>))}</tbody>
          </table></div>
        </Card>
      )}

      {scan?.state === "succeeded" && (
        <Card title="Downloads" aside={`Scan ${scan.scan_id}`}>
          <div className="row">
            {scan.formats.map((f) => <a key={f} className="btn btn-secondary" href={exportUrl(scan.scan_id, f)}>{FORMAT_INFO[f].name}</a>)}
          </div>
          <ul className="muted small" style={{ paddingLeft: 18, marginBottom: 0 }}>
            <li>The JSON report is the complete, unmodified record.</li>
            <li>In the CSV inventory, any cell starting with <code className="mono">= + - @</code> (or a control character) gets a leading apostrophe so spreadsheets do not run it as a formula. A few names therefore differ from the original by that one character. This protects this file only.</li>
            <li>The Markdown summary is plain text and is not escaped. Do not render it as HTML.</li>
            <li>Your review marks are separate: <Link to="/findings">download them from Findings</Link>.</li>
          </ul>
        </Card>
      )}

      <Confirm open={warn} title="Include declaration text?" confirmLabel="Include declarations" onCancel={() => setWarn(false)} onConfirm={() => { setDeclarations(true); setWarn(false); }}>
        Purpose and retention text from your metadata file will be shown in this app and written into the JSON and CSV exports. Only turn this on if everyone who can see the exports may read that text. Scan results can be re-run with it off at any time. Maximum scan time is {formatDuration(config.limits.scan_timeout_seconds)}.
      </Confirm>
    </div>
  );
}
