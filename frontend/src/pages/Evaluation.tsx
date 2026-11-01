import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { EvaluationStatus } from "../api/types";
import { errText, useApp } from "../state/AppState";
import { Button, Card, Chip, Empty, Notice, PageHeader, Spinner } from "../components/ui";
import { ClassTag, Meter, classColor } from "../components/Charts";
import { formatRatio, formatTime, plural } from "../lib/format";

export default function Evaluation() {
  const { config, notify } = useApp();
  const [ev, setEv] = useState<EvaluationStatus | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (!ev || ev.state === "succeeded" || ev.state === "failed") return;
    const t = window.setInterval(async () => {
      try { setEv(await api.get<EvaluationStatus>(`/api/evaluations/${ev.evaluation_id}`)); } catch (e) { notify("error", errText(e)); }
    }, 500);
    return () => window.clearInterval(t);
  }, [ev, notify]);

  if (!config) return <Spinner />;
  async function run() {
    setStarting(true);
    try {
      const { evaluation_id } = await api.post<{ evaluation_id: string }>("/api/evaluations", { dataset: "bundled_synthetic" });
      setEv(await api.get<EvaluationStatus>(`/api/evaluations/${evaluation_id}`));
    } catch (e) { notify("error", errText(e)); }
    setStarting(false);
  }
  const r = ev?.result;
  const running = ev?.state === "queued" || ev?.state === "running";

  return (
    <div className="stack-v">
      <PageHeader title="Evaluation" lead="Per-class precision and recall of the detection rules against the labelled synthetic dataset that ships with the project."
        actions={<Button variant="primary" disabled={!config.benchmark_available || running || starting} onClick={run}>{running ? "Running…" : r ? "Run again" : "Run evaluation"}</Button>} />
      <Notice kind="warn" title="Synthetic benchmark only">
        These figures describe this team’s own synthetic dataset and ground-truth labels. They do not measure accuracy on your uploaded files or on real-world data, and uploaded files are never scored here.
      </Notice>
      {!config.benchmark_available && <Notice kind="error" title="Benchmark data not found">The bundled synthetic data and labels are missing from this installation, so the evaluation cannot run.</Notice>}
      {running && <Spinner label="Evaluating" />}
      {ev?.state === "failed" && <Notice kind="error" title="Evaluation failed">{ev.error}</Notice>}
      {!ev && config.benchmark_available && <Empty title="No evaluation run yet">Run the evaluation to score the current rules. Numbers appear only after a real run.</Empty>}

      {r && (
        <>
          <Card title="Overall (micro-average)" aside={<><Chip>derived</Chip> from the per-class counts below · {formatTime(r.generated_at)}</>}>
            <div className="facts">
              <div className="fact"><b>{formatRatio(r.micro.precision)}</b><span>precision ({r.micro.tp} of {r.micro.predicted} predicted)</span></div>
              <div className="fact"><b>{formatRatio(r.micro.recall)}</b><span>recall ({r.micro.tp} of {r.micro.labelled_positive} labelled)</span></div>
              <div className="fact"><b>{r.micro.fp}</b><span>false positives</span></div>
              <div className="fact"><b>{r.micro.fn}</b><span>false negatives</span></div>
            </div>
            <p className="small muted" style={{ marginTop: 12 }}>{r.scope_notice}</p>
          </Card>

          <Card title="By class" aside={`${plural(r.labelled_source_count, "labelled source")} · ${plural(r.labelled_column_count, "labelled column")} · labels: ${r.label_origin}`}>
            <div className="table-wrap"><table>
              <thead><tr><th>Class</th><th>Precision</th><th>Recall</th><th className="num">TP</th><th className="num">FP</th><th className="num">FN</th></tr></thead>
              <tbody>{r.classes.map((c) => (
                <tr key={c.class_id}>
                  <td><ClassTag id={c.class_id} cls={config.classes.find((k) => k.id === c.class_id)} /></td>
                  <td style={{ minWidth: 170 }}><div className="row" style={{ flexWrap: "nowrap" }}><div style={{ flex: 1 }}>{c.precision !== null && <Meter value={c.precision} max={1} color={classColor(c.class_id)} />}</div><b style={{ minWidth: 52, textAlign: "right" }}>{formatRatio(c.precision)}</b></div></td>
                  <td style={{ minWidth: 170 }}><div className="row" style={{ flexWrap: "nowrap" }}><div style={{ flex: 1 }}>{c.recall !== null && <Meter value={c.recall} max={1} color={classColor(c.class_id)} />}</div><b style={{ minWidth: 52, textAlign: "right" }}>{formatRatio(c.recall)}</b></div></td>
                  <td className="num">{c.true_positives}</td><td className="num">{c.false_positives}</td><td className="num">{c.false_negatives}</td>
                </tr>))}</tbody>
            </table></div>
            <p className="small muted" style={{ marginTop: 10 }}>“n/a” means the ratio is undefined (no predictions or no labelled positives for that class), not zero. The evaluator reports counts only, so individual missed or wrongly flagged columns are not listed.</p>
          </Card>

          <Card title={`Ambiguous columns (${r.ambiguous_columns.length})`}>
            <p className="small muted">Columns the rules assign to more than one class. Each one counts as a separate prediction per class, so an ambiguous column can add a false positive in one class while being correct in another. {r.unscored_finding_count > 0 && `${plural(r.unscored_finding_count, "finding")} in unlabelled sources were not scored.`}</p>
            {r.ambiguous_columns.length > 0 && (
              <div className="table-wrap"><table><thead><tr><th>Column</th><th>Classes</th></tr></thead>
                <tbody>{r.ambiguous_columns.map((a, i) => <tr key={i}><td><code className="mono">{a.column}</code><div className="cell-sub"><code className="mono">{a.source_key}</code> · {a.table}</div></td><td>{a.class_ids.map((id) => config.classes.find((k) => k.id === id)?.label ?? id).join(", ")}</td></tr>)}</tbody></table></div>)}
          </Card>
        </>
      )}
    </div>
  );
}
