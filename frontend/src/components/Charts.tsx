import type { ClassDef, MetadataStatus } from "../api/types";

export const CLASS_COLORS: Record<string, string> = {
  PERSONAL_IDENTITY: "#1F4E79",
  CONTACT_INFORMATION: "#0E7C7B",
  GOVERNMENT_IDENTIFIERS: "#5B4B8A",
  FINANCIAL_INFORMATION: "#A8741A",
  LOCATION_ADDRESS: "#3F7D58",
};
export const classColor = (id: string): string => CLASS_COLORS[id] ?? "#5B7C99";

export const STATUS_ORDER: MetadataStatus[] = ["complete", "missing_purpose", "missing_retention", "missing_both", "no_entry"];
export const STATUS_COLORS: Record<MetadataStatus, string> = {
  complete: "#0E7C7B", missing_purpose: "#D9A441", missing_retention: "#E0B96C", missing_both: "#C2543F", no_entry: "#9AA7B8",
};

export function ClassDot({ id }: { id: string }) {
  return <span className="dot" style={{ background: classColor(id) }} aria-hidden="true" />;
}

export function ClassTag({ cls, id }: { cls?: ClassDef; id: string }) {
  return <span className="class-tag"><ClassDot id={id} />{cls?.label ?? id}</span>;
}

/** Horizontal bar whose width is value / max. The number is always printed beside it. */
export function Meter({ value, max, color = "var(--teal)" }: { value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return <div className="meter" aria-hidden="true"><div className="meter-fill" style={{ width: `${pct}%`, background: color }} /></div>;
}

export function StackedBar({ parts, total, label }: { parts: { key: string; label: string; value: number; color: string }[]; total: number; label: string }) {
  return (
    <div className="stack" role="img" aria-label={label}>
      {parts.filter((p) => p.value > 0).map((p) => (
        <div key={p.key} className="stack-seg" style={{ flexGrow: p.value, background: p.color }} title={`${p.label}: ${p.value} of ${total}`} />
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { key: string; label: string; value: number; color: string }[] }) {
  return (
    <ul className="legend">
      {items.map((i) => (
        <li key={i.key}><span className="dot" style={{ background: i.color }} aria-hidden="true" />{i.label}<b>{i.value.toLocaleString()}</b></li>
      ))}
    </ul>
  );
}
