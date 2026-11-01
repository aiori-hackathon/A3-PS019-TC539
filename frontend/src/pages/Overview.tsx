import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, exportUrl } from "../api/client";
import { useApp, errText } from "../state/AppState";
import { Button, Card, Chip, Empty, LinkButton, Notice, PageHeader, Spinner } from "../components/ui";
import { ClassDot, Legend, Meter, STATUS_COLORS, STATUS_ORDER, StackedBar, classColor } from "../components/Charts";
import { STATUS_LABEL, plural } from "../lib/format";

export default function Overview() {
  const { config, workspace, scan, summary, startScan, notify, refreshWorkspace } = useApp();
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);

  async function loadBundled() {
    setBusy(true);
    try {
      await api.post("/api/workspace/bundled");
      await refreshWorkspace();
      await startScan({ include_declarations: false, formats: ["json", "csv", "md"] });
      nav("/scan");
    } catch (e) { notify("error", errText(e)); }
    finally { setBusy(false); }
  }

  if (!config || !workspace) return <Spinner />;
  const files = workspace.exists ? workspace.files.length : 0;
  const running = scan?.state === "running" || scan?.state === "queued";

  const header = (
    <PageHeader
      title="Personal-data inventory"
      lead="Find columns that look like personal data in CSV files and SQLite databases, see which have a declared purpose and retention period, and review each one."
    />
  );

  if (!summary) {
    return (
      <div className="stack-v">
        {header}
        {running ? (
          <Notice kind="info" title="A scan is running">Open the <Link to="/scan">Scan page</Link> to follow its progress.</Notice>
        ) : files === 0 ? (
          <Empty title="Start with some files" action={
            <div className="row">
              <LinkButton to="/upload">Upload files</LinkButton>
              {config.benchmark_available && <Button variant="primary" disabled={busy} onClick={loadBundled}>{busy ? "Loading…" : "Load bundled synthetic data and scan"}</Button>}
            </div>}>
            Upload CSV files or a SQLite database, or use the synthetic project dataset that ships with FieldLedger. Nothing leaves this computer.
          </Empty>
        ) : (
          <Empty title={`${plural(files, "file")} ready to scan`} action={<LinkButton to="/scan" variant="primary">Configure and run scan</LinkButton>}>
            {scan?.state === "failed" ? "The last scan failed. Open the Scan page for details." : "No scan has been run on these files yet."}
          </Empty>
        )}
        <Notice title="How to read results">{config.notices.evidence}</Notice>
      </div>
    );
  }

  const classes = config.classes;
  const max = Math.max(1, ...Object.values(summary.by_class));
  const m = summary.metadata;
  const gaps = m ? m.by_status.missing_purpose + m.by_status.missing_retention + m.by_status.missing_both + m.by_status.no_entry : 0;
  const reviewed = summary.total_findings - summary.review_counts.unreviewed;
  const sources = Object.entries(summary.by_source);
  const maxSrc = Math.max(1, ...sources.map(([, n]) => n));

  return (
    <div className="stack-v">
      {PageHeader({
        title: "Personal-data inventory",
        lead: "Candidate personal-data columns from the latest scan. Each one is a technical candidate for human review, not a legal conclusion.",
        actions: <>
          <LinkButton to="/findings" variant="primary">Review findings</LinkButton>
          <a className="btn btn-secondary" href={exportUrl(scan!.scan_id, "json")}>Download JSON</a>
        </>,
      })}
      {scan?.stale && <Notice kind="warn" title="Workspace changed since this scan">Files or metadata were added or removed. <Link to="/scan">Run the scan again</Link> to refresh these results.</Notice>}
      {summary.sources_failed > 0 && <Notice kind="error" title={`${plural(summary.sources_failed, "source")} could not be scanned`}>They are not part of these counts. <Link to="/scan">See which ones and why</Link>.</Notice>}

      <div className="facts">
        <div className="fact"><b>{summary.total_findings}</b><span>findings in {plural(summary.sources_scanned, "source")}</span></div>
        <div className="fact"><b>{summary.ambiguous_columns}</b><span>columns matching two classes</span></div>
        <div className="fact"><b>{m ? gaps : "n/a"}</b><span>{m ? "findings with a metadata gap" : "no metadata supplied"}</span></div>
        <div className="fact"><b>{reviewed}</b><span>reviewed by you</span></div>
      </div>

      <div className="grid-2">
        <Card title="Findings by class" aside={<>Taxonomy {summary.taxonomy_status} · rules {summary.rules_status}</>}>
          <div className="ledger" role="table" aria-label="Findings by class">
            <div className="ledger-head" role="row"><span>Class</span><span>Findings</span><span>Total</span><span>{summary.by_class_metadata ? "Metadata coverage" : ""}</span></div>
            {classes.map((c) => {
              const n = summary.by_class[c.id] ?? 0;
              const cov = summary.by_class_metadata?.[c.id];
              return (
                <div className="ledger-row" role="row" key={c.id}>
                  <div className="ledger-name"><span className="class-tag"><ClassDot id={c.id} />{c.label}</span><small>{c.description}</small></div>
                  <div className="meter-wrap"><Meter value={n} max={max} color={classColor(c.id)} /></div>
                  <div className="ledger-n">{n}</div>
                  <div className="cov">
                    {cov && n > 0 ? <StackedBar total={n} label={`Metadata status for ${c.label}`} parts={STATUS_ORDER.map((s) => ({ key: s, label: STATUS_LABEL[s], value: cov[s], color: STATUS_COLORS[s] }))} /> : <span className="muted small">{cov ? "none" : ""}</span>}
                  </div>
                </div>
              );
            })}
          </div>
          {m && <Legend items={STATUS_ORDER.map((s) => ({ key: s, label: STATUS_LABEL[s], value: m.by_status[s], color: STATUS_COLORS[s] }))} />}
        </Card>

        <div className="stack-v">
          <Card title="Findings by source">
            <ul className="plain-list" style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 10 }}>
              {sources.map(([key, n]) => (
                <li key={key}>
                  <div className="spread"><code className="mono">{key}</code><b>{n}</b></div>
                  <Meter value={n} max={maxSrc} />
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Review progress" aside={<Link to="/findings">Open findings</Link>}>
            <div className="row">
              {(["confirmed", "needs_follow_up", "not_personal_data", "unreviewed"] as const).map((s) => (
                <Chip key={s} tone={s === "confirmed" ? "teal" : s === "needs_follow_up" ? "amber" : s === "not_personal_data" ? "navy" : "neutral"}>{STATUS_LABEL[s]}: {summary.review_counts[s]}</Chip>
              ))}
            </div>
            <p className="muted small" style={{ marginTop: 10 }}>Review marks are local notes in the temporary workspace. They never change detection results or reports.</p>
          </Card>
        </div>
      </div>
      <Notice title="Heuristic detection">{summary.notices.heuristic}</Notice>
    </div>
  );
}
