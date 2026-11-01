export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KiB", "MiB", "GiB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

/** Ratios may be null (undefined precision or recall). Never turn null into 0. */
export function formatRatio(v: number | null | undefined): string {
  return v === null || v === undefined ? "n/a" : `${(v * 100).toFixed(1)}%`;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

export function formatDuration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)} seconds`;
  const m = Math.round(seconds / 60);
  return m < 90 ? `${m} minutes` : `${Math.round(m / 60)} hours`;
}

export function humanize(code: string): string {
  const t = code.replace(/_/g, " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export const STATUS_LABEL: Record<string, string> = {
  complete: "Complete",
  missing_purpose: "Purpose missing",
  missing_retention: "Retention missing",
  missing_both: "Declares neither",
  no_entry: "No entry",
  unreviewed: "Unreviewed",
  confirmed: "Confirmed",
  not_personal_data: "Not personal data",
  needs_follow_up: "Needs follow-up",
};

export const STAGE_LABEL: Record<string, string> = {
  discovering: "Discovering files",
  profiling: "Reading structure",
  detecting: "Applying rules",
  linking_metadata: "Linking metadata",
  writing_reports: "Writing reports",
};

export const SOURCE_ERROR_LABEL: Record<string, string> = {
  unsupported_type: "Unsupported file type",
  input_access: "Cannot read file",
  input_too_large: "Over a size or row limit",
  malformed_csv: "Malformed CSV",
  invalid_database: "Invalid SQLite database",
  database_timeout: "Database timed out",
  path_access: "Unsafe path",
  ingest_failed: "Could not process",
};

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}
