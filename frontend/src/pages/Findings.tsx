import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, exportUrl, findingsQuery, REVIEWS_EXPORT_URL } from "../api/client";
import type { FindingDetail, FindingPage, MetadataStatus, ReviewStatus } from "../api/types";
import { errText, useApp } from "../state/AppState";
import { useAsync, useDebounced } from "../lib/useAsync";
import { Button, Card, Chip, Confirm, ErrorBox, FilterChip, Mono, NeedsScan, Notice, PageHeader, Pagination, Spinner, StatusChip } from "../components/ui";
import { ClassTag } from "../components/Charts";
import { STATUS_LABEL, formatTime } from "../lib/format";

const SORTS: [string, string][] = [["source", "Source"], ["table", "Table"], ["column", "Column"], ["class", "Class"], ["position", "Position"]];
const META_STATUSES = ["complete", "missing_purpose", "missing_retention", "missing_both", "no_entry"];
const REVIEW_STATUSES: ReviewStatus[] = ["unreviewed", "confirmed", "needs_follow_up", "not_personal_data"];

export default function Findings() {
  const { config, scan, summary, scanReady, refreshSummary, refreshWorkspace, notify } = useApp();
  const [params, setParams] = useSearchParams();
  const classes = params.getAll("class");
  const metaSel = params.getAll("metadata_status");
  const revSel = params.getAll("review_status");
  const source = params.get("source") ?? "";
  const ambiguous = params.get("ambiguous") ?? "";
  const sort = params.get("sort") ?? "source";
  const order = params.get("order") ?? "asc";
  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const pageSize = [25, 50, 100, 200].includes(Number(params.get("page_size"))) ? Number(params.get("page_size")) : 50;
  const openId = params.get("open");

  const [qText, setQText] = useState(params.get("q") ?? "");
  const q = useDebounced(qText, 250);
  const [confirmReset, setConfirmReset] = useState(false);

  function update(mut: (p: URLSearchParams) => void, keepPage = false) {
    const next = new URLSearchParams(params);
    mut(next);
    if (!keepPage) next.delete("page");
    setParams(next, { replace: true });
  }
  useEffect(() => { if (q !== (params.get("q") ?? "")) update((p) => (q ? p.set("q", q) : p.delete("q"))); /* eslint-disable-next-line */ }, [q]);
  const toggleIn = (key: string, value: string) => update((p) => {
    const cur = p.getAll(key); p.delete(key);
    (cur.includes(value) ? cur.filter((v) => v !== value) : [...cur, value]).forEach((v) => p.append(key, v));
  });

  const query = findingsQuery({ q: params.get("q") ?? "", class: classes, source, metadata_status: metaSel, review_status: revSel, ambiguous, sort, order, page, page_size: pageSize });
  const list = useAsync(() => api.get<FindingPage>(`/api/scans/${scan!.scan_id}/findings?${query}`), [scan?.scan_id, query], scanReady);

  const items = list.data?.items ?? [];
  const idx = items.findIndex((i) => i.id === openId);
  const hasMeta = summary?.metadata != null;
  const filtersOn = Boolean(params.get("q") || classes.length || metaSel.length || revSel.length || source || ambiguous);

  if (!scanReady) return <div className="stack-v"><PageHeader title="Findings" lead="Every column a rule matched, with search, filters and review." /><NeedsScan what="Findings" /></div>;
  if (!config || !summary) return <Spinner />;

  async function afterReview() { list.reload(); await refreshSummary(); await refreshWorkspace(); }

  return (
    <div className="stack-v">
      <PageHeader
        title="Findings"
        lead="Each row is one column matched to one class. A column that fits two classes appears twice. Open a row to see why it matched and to record your review."
        actions={<>
          {scan!.formats.map((f) => <a key={f} className="btn btn-secondary" href={exportUrl(scan!.scan_id, f)}>Export {f.toUpperCase()}</a>)}
        </>}
      />
      {scan!.stale && <Notice kind="warn" title="Workspace changed since this scan">These results describe an earlier set of files. Run the scan again to update them.</Notice>}

      <Card>
        <div className="toolbar">
          <div className="toolbar-row">
            <input type="search" placeholder="Search source, table or column" aria-label="Search findings" value={qText} onChange={(e) => setQText(e.target.value)} />
            <select aria-label="Source" value={source} onChange={(e) => update((p) => (e.target.value ? p.set("source", e.target.value) : p.delete("source")))}>
              <option value="">All sources</option>
              {Object.keys(summary.by_source).map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select aria-label="Sort by" value={sort} onChange={(e) => update((p) => p.set("sort", e.target.value))}>
              {SORTS.map(([v, l]) => <option key={v} value={v}>Sort: {l}</option>)}
            </select>
            <Button small onClick={() => update((p) => p.set("order", order === "asc" ? "desc" : "asc"))}>{order === "asc" ? "Ascending" : "Descending"}</Button>
            {filtersOn && <Button small variant="ghost" onClick={() => { setQText(""); setParams(new URLSearchParams(), { replace: true }); }}>Clear filters</Button>}
          </div>
          <div className="toolbar-row"><span className="toolbar-label">Class</span>
            {config.classes.map((c) => <FilterChip key={c.id} active={classes.includes(c.id)} onClick={() => toggleIn("class", c.id)} count={summary.by_class[c.id]}>{c.label}</FilterChip>)}
            <FilterChip active={ambiguous === "true"} onClick={() => update((p) => (ambiguous === "true" ? p.delete("ambiguous") : p.set("ambiguous", "true")))}>Ambiguous only</FilterChip>
          </div>
          {hasMeta && <div className="toolbar-row"><span className="toolbar-label">Metadata</span>
            {META_STATUSES.map((s) => <FilterChip key={s} active={metaSel.includes(s)} onClick={() => toggleIn("metadata_status", s)} count={summary.metadata?.by_status[s as MetadataStatus]}>{STATUS_LABEL[s]}</FilterChip>)}
          </div>}
          <div className="toolbar-row"><span className="toolbar-label">Review</span>
            {REVIEW_STATUSES.map((s) => <FilterChip key={s} active={revSel.includes(s)} onClick={() => toggleIn("review_status", s)} count={summary.review_counts[s]}>{STATUS_LABEL[s]}</FilterChip>)}
          </div>
        </div>

        {list.error ? <ErrorBox message={list.error} onRetry={list.reload} /> : list.loading && !list.data ? <Spinner /> : (
          <>
            <div className="table-wrap"><table>
              <thead><tr><th>Column</th><th>Class</th><th>Rules</th>{hasMeta && <th>Metadata</th>}<th>Review</th></tr></thead>
              <tbody>
                {items.map((f) => (
                  <tr key={f.id} className={`clickable${f.id === openId ? " selected" : ""}`} onClick={() => update((p) => p.set("open", f.id), true)}>
                    <td><button className="link-btn" style={{ textDecoration: "none", color: "inherit", textAlign: "left" }} onClick={(e) => { e.stopPropagation(); update((p) => p.set("open", f.id), true); }}><Mono>{f.column}</Mono></button>
                      <div className="cell-sub"><code className="mono">{f.source_key}</code>{f.table !== f.source_key.replace(/\.[^.]+$/, "").split("/").pop() && <> · {f.table}</>}</div></td>
                    <td><ClassTag id={f.class_id} cls={config.classes.find((c) => c.id === f.class_id)} />{f.ambiguous && <div className="cell-sub">Also matches {f.other_class_ids.map((id) => config.classes.find((c) => c.id === id)?.label ?? id).join(", ")}</div>}</td>
                    <td className="cell-sub">{f.rule_ids.map((r) => <div key={r}><code className="mono">{r}</code></div>)}</td>
                    {hasMeta && <td><StatusChip status={f.metadata_status} /></td>}
                    <td><StatusChip status={f.review_status} /></td>
                  </tr>))}
                {items.length === 0 && <tr><td colSpan={hasMeta ? 5 : 4} className="muted">{filtersOn ? "No findings match these filters." : "This scan produced no findings."}</td></tr>}
              </tbody>
            </table></div>
            <Pagination page={page} pageSize={pageSize} total={list.data?.total ?? 0} onPage={(p) => update((s) => s.set("page", String(p)), true)} />
            <div className="row" style={{ marginTop: 8 }}>
              <label className="small muted">Rows per page{" "}
                <select value={pageSize} onChange={(e) => update((p) => p.set("page_size", e.target.value))}>{[25, 50, 100, 200].map((n) => <option key={n}>{n}</option>)}</select></label>
            </div>
          </>
        )}
      </Card>

      <Card title="Your review marks">
        <p className="small muted">Marks are local notes kept in the temporary workspace ({summary.review_counts.unreviewed === summary.total_findings ? "none yet" : `${summary.total_findings - summary.review_counts.unreviewed} set`}). They do not change findings, counts or any export of the scan.</p>
        <div className="row">
          <a className="btn btn-secondary" href={REVIEWS_EXPORT_URL}>Download review annotations</a>
          <Button variant="ghost" disabled={summary.total_findings === summary.review_counts.unreviewed} onClick={() => setConfirmReset(true)}>Reset all marks</Button>
        </div>
      </Card>

      <Confirm open={confirmReset} title="Reset all review marks?" confirmLabel="Reset marks" danger onCancel={() => setConfirmReset(false)}
        onConfirm={async () => { setConfirmReset(false); try { const r = await api.del<{ removed: number }>("/api/workspace/reviews"); notify("ok", `${r.removed} review marks removed.`); await afterReview(); } catch (e) { notify("error", errText(e)); } }}>
        This removes {summary.total_findings - summary.review_counts.unreviewed} review marks. They are stored only in the temporary workspace, so they cannot be recovered.
      </Confirm>

      {openId && (
        <Drawer scanId={scan!.scan_id} id={openId}
          onClose={() => update((p) => p.delete("open"), true)}
          onPrev={idx > 0 ? () => update((p) => p.set("open", items[idx - 1].id), true) : undefined}
          onNext={idx >= 0 && idx < items.length - 1 ? () => update((p) => p.set("open", items[idx + 1].id), true) : undefined}
          onOpen={(id) => update((p) => p.set("open", id), true)}
          onReviewed={afterReview} />
      )}
    </div>
  );
}

function Drawer({ scanId, id, onClose, onPrev, onNext, onOpen, onReviewed }: {
  scanId: string; id: string; onClose: () => void; onPrev?: () => void; onNext?: () => void; onOpen: (id: string) => void; onReviewed: () => Promise<void>;
}) {
  const { config, notify } = useApp();
  const { data: d, error, loading, reload } = useAsync(() => api.get<FindingDetail>(`/api/scans/${scanId}/findings/${id}`), [scanId, id]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const matchedPatterns = useMemo(() => new Set(d?.explanation.kind === "matched_pattern" ? d.explanation.matches.map((m) => `${m.rule_id}|${m.pattern}`) : []), [d]);

  async function setReview(status: ReviewStatus) {
    try { await api.put(`/api/scans/${scanId}/findings/${id}/review`, { status }); reload(); await onReviewed(); notify("ok", status === "unreviewed" ? "Review mark removed." : "Review saved."); }
    catch (e) { notify("error", errText(e)); }
  }
  const cls = (cid: string) => config?.classes.find((c) => c.id === cid);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Finding detail">
        <div className="drawer-head">
          <div><h2>{d?.column ?? "Loading…"}</h2>{d && <div className="row" style={{ marginTop: 6 }}><ClassTag id={d.class_id} cls={cls(d.class_id)} />{d.ambiguous && <Chip tone="amber">ambiguous</Chip>}</div>}</div>
          <Button small variant="ghost" onClick={onClose} aria-label="Close detail">Close</Button>
        </div>
        <div className="drawer-body">
          {loading && !d && <Spinner />}
          {error && <ErrorBox message={error} onRetry={reload} />}
          {d && (<>
            <dl className="kv">
              <dt>Source</dt><dd><Mono>{d.source_key}</Mono></dd>
              <dt>Table</dt><dd><Mono>{d.table}</Mono> · column {d.position}</dd>
              <dt>Evidence</dt><dd>The column name matched a rule. No cell values were read.</dd>
              {d.metadata_status && <><dt>Metadata</dt><dd><StatusChip status={d.metadata_status} /></dd></>}
              {d.declaration && <><dt>Purpose</dt><dd>{d.declaration.purpose ?? <span className="muted">not declared</span>}</dd><dt>Retention</dt><dd>{d.declaration.retention ?? <span className="muted">not declared</span>}</dd></>}
            </dl>

            <div>
              <div className="sec-title">Why it matched</div>
              <div className="tokens" style={{ marginBottom: 10 }}>{d.column_tokens.map((t, i) => <code key={i}>{t}</code>)}</div>
              {d.explanation.kind === "matched_pattern"
                ? <p>{d.explanation.matches.map((m, i) => <span key={i}>{i > 0 && " "}Rule <Mono>{m.rule_id}</Mono> matched the pattern <span className="pattern-hit"><Mono>{m.pattern}</Mono></span>.</span>)}</p>
                : <p>The rule matched this column name. The exact pattern could not be reproduced here, so the rule’s patterns are listed below instead.</p>}
              {d.rules.map((r) => (
                <div className="rule-box" key={r.id} style={{ marginTop: 8 }}>
                  <div className="spread"><Mono>{r.id}</Mono><Chip>{r.match === "token" ? "token match" : "exact match"}</Chip></div>
                  <div className="row" style={{ gap: 6 }}>{r.patterns.map((p) => <span key={p} className={matchedPatterns.has(`${r.id}|${p}`) ? "pattern-hit" : "chip"}><Mono>{p}</Mono></span>)}</div>
                  {r.exclude_patterns.length > 0 && <div className="small muted">Ignored when the name also contains: {r.exclude_patterns.join(", ")}</div>}
                </div>))}
              {d.suppressed_rules.length > 0 && <p className="small muted" style={{ marginTop: 8 }}>Other rules also matched this name but were suppressed by an exclusion: {d.suppressed_rules.map((s) => `${s.rule_id} (${s.excluded_by.join(", ")})`).join("; ")}.</p>}
            </div>

            {d.column_profile && (
              <div><div className="sec-title">Column structure</div>
                <dl className="kv">
                  <dt>Type</dt><dd>{d.column_profile.inferred_type}{d.column_profile.declared_type ? ` (declared ${d.column_profile.declared_type})` : ""}</dd>
                  <dt>Non-null / null</dt><dd>{d.column_profile.non_null_count ?? "-"} / {d.column_profile.null_count ?? "-"}</dd>
                  <dt>Table rows</dt><dd>{d.column_profile.table_row_count ?? "unknown"}</dd>
                </dl></div>)}

            {d.siblings.length > 0 && (
              <div><div className="sec-title">Same column, other class</div>
                <div className="row">{d.siblings.map((s) => <Button key={s.id} small onClick={() => onOpen(s.id)}>{cls(s.class_id)?.label ?? s.class_id}</Button>)}</div></div>)}

            <div>
              <div className="sec-title">Your review</div>
              <div className="review-set">
                {(["confirmed", "needs_follow_up", "not_personal_data", "unreviewed"] as ReviewStatus[]).map((s) => (
                  <button key={s} type="button" className={`fchip${d.review_status === s ? " on" : ""}`} aria-pressed={d.review_status === s} onClick={() => setReview(s)}>{s === "unreviewed" ? "Clear mark" : STATUS_LABEL[s]}</button>))}
              </div>
              <p className="small muted" style={{ marginTop: 8 }}>{d.review ? `Marked ${formatTime(d.review.updated_at)}. ` : ""}Marking a finding “not personal data” does not remove it from results or exports.</p>
            </div>
            <Notice>{d.notice}</Notice>
          </>)}
        </div>
        <div className="drawer-foot"><Button small disabled={!onPrev} onClick={onPrev}>Previous</Button><Button small disabled={!onNext} onClick={onNext}>Next</Button></div>
      </aside>
    </>
  );
}
