import { useEffect, useRef, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { STATUS_LABEL } from "../lib/format";

export function PageHeader({ title, lead, actions }: { title: string; lead?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {lead && <p className="lead">{lead}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Card({ title, aside, children, className = "" }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || aside) && <div className="card-head"><h2>{title}</h2>{aside && <div className="card-aside">{aside}</div>}</div>}
      {children}
    </section>
  );
}

export function Notice({ kind = "info", title, children }: { kind?: "info" | "warn" | "error" | "ok"; title?: string; children: ReactNode }) {
  return (
    <div className={`notice notice-${kind}`} role={kind === "error" ? "alert" : undefined}>
      {title && <strong>{title}</strong>}
      <div>{children}</div>
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <div className="spinner-row" role="status"><span className="spinner" aria-hidden="true" />{label}…</div>;
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <Notice kind="error" title="Could not load this">
      {message} {onRetry && <button className="link-btn" onClick={onRetry}>Try again</button>}
    </Notice>
  );
}

type BtnProps = { variant?: "primary" | "secondary" | "ghost" | "danger"; small?: boolean } & React.ComponentPropsWithRef<"button">;
export function Button({ variant = "secondary", small, className = "", ...rest }: BtnProps) {
  return <button {...rest} className={`btn btn-${variant}${small ? " btn-sm" : ""} ${className}`} />;
}

export function LinkButton({ to, variant = "secondary", children }: { to: string; variant?: "primary" | "secondary"; children: ReactNode }) {
  return <Link className={`btn btn-${variant}`} to={to}>{children}</Link>;
}

export function Chip({ children, tone = "neutral", title }: { children: ReactNode; tone?: "neutral" | "teal" | "amber" | "red" | "navy"; title?: string }) {
  return <span className={`chip chip-${tone}`} title={title}>{children}</span>;
}

export function FilterChip({ active, onClick, children, count }: { active: boolean; onClick: () => void; children: ReactNode; count?: number }) {
  return (
    <button type="button" className={`fchip${active ? " on" : ""}`} aria-pressed={active} onClick={onClick}>
      {children}{count !== undefined && <span className="fchip-n">{count.toLocaleString()}</span>}
    </button>
  );
}

const STATUS_TONE: Record<string, "neutral" | "teal" | "amber" | "red" | "navy"> = {
  complete: "teal", missing_purpose: "amber", missing_retention: "amber", missing_both: "red", no_entry: "red",
  unreviewed: "neutral", confirmed: "teal", not_personal_data: "navy", needs_follow_up: "amber",
};
export function StatusChip({ status }: { status: string | null }) {
  if (!status) return <span className="muted">-</span>;
  return <Chip tone={STATUS_TONE[status] ?? "neutral"}>{STATUS_LABEL[status] ?? status}</Chip>;
}

export function Mono({ children }: { children: ReactNode }) {
  return <code className="mono">{children}</code>;
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(total, page * pageSize);
  return (
    <nav className="pager" aria-label="Pagination">
      <span>{from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()}</span>
      <div>
        <Button small disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
        <span className="pager-page">Page {page} of {pages}</span>
        <Button small disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button>
      </div>
    </nav>
  );
}

/** Modal confirmation. Escape and the backdrop cancel. */
export function Confirm({ open, title, children, confirmLabel, danger, onConfirm, onCancel }: {
  open: boolean; title: string; children: ReactNode; confirmLabel: string; danger?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onCancel]);
  if (!open) return null;
  return (
    <div className="modal-backdrop" onMouseDown={onCancel}>
      <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" onMouseDown={(e) => e.stopPropagation()}>
        <h2 id="confirm-title">{title}</h2>
        <div className="modal-body">{children}</div>
        <div className="modal-actions">
          <Button onClick={onCancel}>Cancel</Button>
          <Button ref={ref} variant={danger ? "danger" : "primary"} onClick={onConfirm}>{confirmLabel}</Button>
        </div>
      </div>
    </div>
  );
}

export function NeedsScan({ what }: { what: string }) {
  return (
    <Empty title="No scan results yet" action={<div className="row"><LinkButton to="/upload">Add files</LinkButton><LinkButton to="/scan" variant="primary">Go to scan</LinkButton></div>}>
      {what} appear here after a scan finishes.
    </Empty>
  );
}
