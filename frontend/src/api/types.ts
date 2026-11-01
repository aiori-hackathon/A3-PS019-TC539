// Types mirror docs/api-contract.md (API v1). They describe structure only; no cell values exist.

export type ClassId = string;
export type MetadataStatus = "complete" | "missing_purpose" | "missing_retention" | "missing_both" | "no_entry";
export type ReviewStatus = "unreviewed" | "confirmed" | "not_personal_data" | "needs_follow_up";
export type ScanState = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type ScanStage = "discovering" | "profiling" | "detecting" | "linking_metadata" | "writing_reports";
export type ReportFormat = "json" | "csv" | "md";

export interface ClassDef { id: ClassId; label: string; description: string }
export interface RuleDef { id: string; class_id: ClassId; match: "token" | "exact"; patterns: string[]; exclude_patterns: string[] }

export interface Limits {
  max_file_bytes: number; max_rows: number; max_columns: number; max_line_chars: number;
  max_db_bytes: number; max_tables: number; sqlite_timeout_seconds: number;
  max_files: number; max_workspace_bytes: number; max_files_per_request: number;
  max_metadata_bytes: number; idle_timeout_seconds: number; scan_timeout_seconds: number;
}

export interface AppConfig {
  version: string; api_version: number;
  taxonomy_status: string; rules_status: string;
  classes: ClassDef[]; rules: RuleDef[]; limits: Limits;
  supported_suffixes: string[]; report_formats: ReportFormat[]; metadata_origins: string[];
  notices: { evidence: string; heuristic: string; gap: string };
  csv_notes: string[]; review_statuses: ReviewStatus[]; benchmark_available: boolean;
}

export interface WorkspaceFile { source_key: string; type: "csv" | "sqlite"; size_bytes: number }
export type WorkspaceState =
  | { exists: false }
  | {
      exists: true; revision: number; files: WorkspaceFile[];
      metadata: null | { entries: number; origin: string };
      review_count: number; expires_at: string; idle_timeout_seconds: number;
      latest_scan_id: string | null;
    };

export interface UploadResult { name: string; accepted: boolean; code?: string; message?: string }
export interface UploadResponse { results: UploadResult[]; revision: number }

export interface SourceError { source_key: string; code: string; message: string }
export interface ScanStatus {
  scan_id: string; state: ScanState; stage: ScanStage | null;
  sources_total: number | null; sources_done: number; source_errors: SourceError[];
  workspace_revision: number; stale: boolean; started_at: string; finished_at: string | null;
  error: string | null; formats: ReportFormat[]; include_declarations: boolean;
}

export interface MetadataSummary {
  flagged_columns: number; ambiguous_columns: number; by_status: Record<MetadataStatus, number>;
  metadata_entries: number; orphan_entries: number; unmatched_entries: number;
}
export interface ScanSummary {
  generated_at: string; sources_scanned: number; sources_failed: number; total_findings: number;
  by_class: Record<ClassId, number>; by_source: Record<string, number>; ambiguous_columns: number;
  metadata: MetadataSummary | null;
  by_class_metadata: Record<ClassId, Record<MetadataStatus, number>> | null;
  review_counts: Record<ReviewStatus, number>;
  taxonomy_status: string; rules_status: string; include_declarations: boolean; formats: ReportFormat[];
  notices: { evidence: string; heuristic: string };
}

export interface ColumnInfo {
  name: string; position: number; inferred_type: string; declared_type: string | null;
  non_null_count: number | null; null_count: number | null; finding_count: number;
}
export interface TableInfo { name: string; row_count: number | null; columns: ColumnInfo[] }
export interface SourceInfo {
  source_key: string; source_type: "csv" | "sqlite"; type_basis: string; finding_count: number;
  warnings: string[]; tables: TableInfo[];
}

export interface FindingItem {
  id: string; source_key: string; table: string; column: string; position: number;
  class_id: ClassId; class_label: string; rule_ids: string[]; evidence_kind: string;
  ambiguous: boolean; other_class_ids: ClassId[];
  metadata_status: MetadataStatus | null; has_purpose: boolean | null; has_retention: boolean | null;
  review_status: ReviewStatus;
}
export interface FindingPage { items: FindingItem[]; total: number; page: number; page_size: number }
export interface FindingDetail extends FindingItem {
  rules: RuleDef[]; column_tokens: string[];
  explanation: { kind: "matched_pattern"; matches: { rule_id: string; pattern: string }[] } | { kind: "rule_patterns_only" };
  suppressed_rules: { rule_id: string; class_id: ClassId; excluded_by: string[] }[];
  column_profile: null | { inferred_type: string; declared_type: string | null; non_null_count: number | null; null_count: number | null; table_row_count: number | null };
  siblings: { id: string; class_id: ClassId }[];
  declaration?: { purpose: string | null; retention: string | null };
  review: null | { status: ReviewStatus; updated_at: string };
  notice: string;
}

export interface LinkedField {
  source_key: string; table: string; column: string; position: number; class_ids: ClassId[];
  ambiguous: boolean; status: MetadataStatus; has_purpose: boolean; has_retention: boolean;
  purpose?: string | null; retention?: string | null; id_by_class: Record<ClassId, string | null>;
}
export interface MetaEntry { source_key: string; table: string; column: string; has_purpose: boolean; has_retention: boolean; purpose?: string | null; retention?: string | null }
export type MetadataReport =
  | { available: false }
  | {
      available: true; metadata_origin: string; summary: MetadataSummary; declarations_included: boolean;
      linked_fields: LinkedField[]; orphan_entries: MetaEntry[];
      unmatched_entries: (MetaEntry & { reason: string; case_variant_exists: boolean })[];
      review_gap_notice: string; technical_evidence_notice: string;
    };

export interface ClassMetrics {
  class_id: ClassId; true_positives: number; false_positives: number; false_negatives: number;
  precision: number | null; recall: number | null;
}
export interface EvaluationResult {
  scope: "synthetic_benchmark"; scope_notice: string; label_origin: string;
  taxonomy_status: string; rules_status: string;
  labelled_source_count: number; labelled_column_count: number; unscored_finding_count: number;
  ambiguous_columns: { source_key: string; table: string; column: string; class_ids: ClassId[] }[];
  classes: ClassMetrics[];
  micro: { tp: number; fp: number; fn: number; predicted: number; labelled_positive: number; precision: number | null; recall: number | null; derived: true };
  generated_at: string;
}
export interface EvaluationStatus {
  evaluation_id: string; state: "queued" | "running" | "succeeded" | "failed";
  result: EvaluationResult | null; error: string | null; started_at: string; finished_at: string | null;
}
