"""Report generation for FieldLedger scan and evaluation results.

Output files
------------
fieldledger_report.json   Full scan results (findings, metadata gaps, notices).
fieldledger_inventory.csv One row per finding: source_key, table, column, position, class_id,
                          rule_ids, plus optional metadata status/declarations.
fieldledger_summary.md    Human-readable Markdown summary.
fieldledger_evaluation.json  Evaluation metrics (only when evaluation is requested).

Privacy: no raw dataset values, no absolute paths, declaration text only when
``include_declarations=True``.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .evaluation import EvaluationReport
from .findings import EVIDENCE_NOTICE, Finding
from .metadata import GAP_NOTICE, GapReport
from .classes import ClassRegistry
from .profiles import SourceProfile
from .rules import RuleSet

_JSON_NAME = "fieldledger_report.json"
_CSV_NAME = "fieldledger_inventory.csv"
_MD_NAME = "fieldledger_summary.md"
_EVAL_NAME = "fieldledger_evaluation.json"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _build_scan_dict(
    findings: Sequence[Finding],
    profiles_by_key: dict[str, SourceProfile],
    gap_report: GapReport | None,
    registry: ClassRegistry,
    ruleset: RuleSet,
    *,
    include_declarations: bool = False,
) -> dict[str, Any]:
    """Build the JSON-serialisable scan report dictionary."""
    report: dict[str, Any] = {
        "fieldledger_version": __version__,
        "generated_at": _timestamp(),
        "technical_evidence_notice": EVIDENCE_NOTICE,
        "taxonomy_status": registry.taxonomy_status,
        "rules_status": ruleset.rules_status,
        "sources_scanned": len(profiles_by_key),
        "total_findings": len(findings),
        "findings": [f.to_dict() for f in findings],
    }

    if gap_report is not None:
        report["review_gap_notice"] = GAP_NOTICE
        report["declarations_included"] = include_declarations
        report["metadata"] = gap_report.to_dict(include_declarations=include_declarations)

    return report


def _write_json(
    findings: Sequence[Finding],
    profiles_by_key: dict[str, SourceProfile],
    gap_report: GapReport | None,
    registry: ClassRegistry,
    ruleset: RuleSet,
    out_dir: Path,
    *,
    include_declarations: bool = False,
) -> Path:
    """Write fieldledger_report.json."""
    data = _build_scan_dict(
        findings,
        profiles_by_key,
        gap_report,
        registry,
        ruleset,
        include_declarations=include_declarations,
    )
    path = out_dir / _JSON_NAME
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _write_csv(
    findings: Sequence[Finding],
    gap_report: GapReport | None,
    out_dir: Path,
    *,
    include_declarations: bool = False,
) -> Path:
    """Write fieldledger_inventory.csv."""
    # Build a lookup from column identity to linked-field status
    linked: dict[tuple[str, str, str], Any] = {}
    if gap_report is not None:
        for lf in gap_report.linked_fields:
            linked[lf.column_identity] = lf

    base_fields = [
        "source_key",
        "table",
        "column",
        "position",
        "class_id",
        "rule_ids",
        "evidence_kind",
    ]
    meta_fields = ["status", "has_purpose", "has_retention"]
    decl_fields = ["purpose", "retention"] if include_declarations else []
    fieldnames = base_fields + (meta_fields + decl_fields if gap_report is not None else [])

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()

    for f in findings:
        row: dict[str, Any] = {
            "source_key": f.source_key,
            "table": f.table_name,
            "column": f.column_name,
            "position": f.column_position,
            "class_id": f.class_id,
            "rule_ids": ";".join(f.rule_ids),
            "evidence_kind": f.evidence_kind,
        }
        lf = linked.get(f.column_identity)
        if lf is not None:
            row["status"] = lf.status.value
            row["has_purpose"] = lf.has_purpose
            row["has_retention"] = lf.has_retention
            if include_declarations:
                row["purpose"] = (lf.entry.purpose if lf.entry else "") or ""
                row["retention"] = (lf.entry.retention if lf.entry else "") or ""
        elif gap_report is not None:
            row["status"] = ""
            row["has_purpose"] = ""
            row["has_retention"] = ""
            if include_declarations:
                row["purpose"] = ""
                row["retention"] = ""

        writer.writerow(row)

    path = out_dir / _CSV_NAME
    path.write_text(buf.getvalue(), encoding="utf-8")
    return path


def _write_md(
    findings: Sequence[Finding],
    profiles_by_key: dict[str, SourceProfile],
    gap_report: GapReport | None,
    registry: ClassRegistry,
    ruleset: RuleSet,
    out_dir: Path,
) -> Path:
    """Write fieldledger_summary.md."""
    lines: list[str] = []
    lines.append("# FieldLedger Scan Summary\n")
    lines.append(f"> {EVIDENCE_NOTICE}\n")
    lines.append(f"- **Version**: {__version__}")
    lines.append(f"- **Generated**: {_timestamp()}")
    lines.append(f"- **Taxonomy status**: {registry.taxonomy_status}")
    lines.append(f"- **Rules status**: {ruleset.rules_status}")
    lines.append(f"- **Sources scanned**: {len(profiles_by_key)}")
    lines.append(f"- **Total findings**: {len(findings)}")
    lines.append("")

    # Per-class summary
    class_counts: dict[str, int] = {}
    for f in findings:
        class_counts[f.class_id] = class_counts.get(f.class_id, 0) + 1

    if class_counts:
        lines.append("## Findings by class\n")
        lines.append("| Class | Findings |")
        lines.append("|---|---|")
        for cdef in registry.classes:
            count = class_counts.get(cdef.id, 0)
            lines.append(f"| {cdef.label} (`{cdef.id}`) | {count} |")
        lines.append("")

    # Per-source summary
    source_counts: dict[str, int] = {}
    for f in findings:
        source_counts[f.source_key] = source_counts.get(f.source_key, 0) + 1

    if source_counts:
        lines.append("## Findings by source\n")
        lines.append("| Source | Findings |")
        lines.append("|---|---|")
        for key in sorted(source_counts):
            lines.append(f"| `{key}` | {source_counts[key]} |")
        lines.append("")

    # Metadata gap summary
    if gap_report is not None:
        lines.append("## Metadata gap summary\n")
        lines.append(f"> {GAP_NOTICE}\n")
        s = gap_report.summary
        lines.append(f"- **Flagged columns**: {s.flagged_columns}")
        lines.append(f"- **Ambiguous columns**: {s.ambiguous_columns}")
        for status_val, count in s.by_status:
            lines.append(f"- **{status_val}**: {count}")
        lines.append(f"- **Metadata entries**: {s.metadata_entries}")
        lines.append(f"- **Orphan entries**: {s.orphan_entries}")
        lines.append(f"- **Unmatched entries**: {s.unmatched_entries}")
        lines.append("")

    path = out_dir / _MD_NAME
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_reports(
    findings: Sequence[Finding],
    profiles_by_key: dict[str, SourceProfile],
    gap_report: GapReport | None,
    registry: ClassRegistry,
    ruleset: RuleSet,
    out_dir: Path,
    formats: list[str],
    include_declarations: bool = False,
) -> list[Path]:
    """Write requested report files and return a list of paths written."""
    written: list[Path] = []

    if "json" in formats:
        written.append(
            _write_json(
                findings,
                profiles_by_key,
                gap_report,
                registry,
                ruleset,
                out_dir,
                include_declarations=include_declarations,
            )
        )

    if "csv" in formats:
        written.append(
            _write_csv(
                findings,
                gap_report,
                out_dir,
                include_declarations=include_declarations,
            )
        )

    if "md" in formats:
        written.append(
            _write_md(
                findings,
                profiles_by_key,
                gap_report,
                registry,
                ruleset,
                out_dir,
            )
        )

    return written


def write_evaluation_report(report: EvaluationReport, out_dir: Path) -> Path:
    """Write fieldledger_evaluation.json and return the path."""
    data = {
        "fieldledger_version": __version__,
        "generated_at": _timestamp(),
        **report.to_dict(),
    }
    path = out_dir / _EVAL_NAME
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
