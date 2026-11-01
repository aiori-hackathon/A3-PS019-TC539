"""Service layer: composes the existing public FieldLedger functions for the web API.

This module is a thin adapter. It does not reimplement detection, rules, metadata linking,
evaluation or report writing; it calls ``profile_source``, ``detect_candidates``,
``explain_column``, ``load_metadata``, ``link_metadata``, ``write_reports``, ``load_labels`` and
``evaluate`` and reshapes their results (docs/api-contract.md).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__
from ..classes import ClassRegistry, load_class_registry
from ..detector import ambiguous_columns, detect_candidates, explain_column
from ..discovery import discover_sources
from ..errors import ConfigError, FieldLedgerError
from ..evaluation import evaluate, load_labels
from ..findings import EVIDENCE_NOTICE, Finding, source_key_for
from ..limits import IngestionLimits
from ..metadata import GAP_NOTICE, GapReport, link_metadata, load_metadata
from ..profiler import profile_source
from ..profiles import SourceProfile
from ..report import write_reports
from ..rules import RuleSet, load_rule_set, tokenize_name
from . import contract
from .apierrors import ApiError
from .csvsafe import neutralise_csv_text
from .workspace import Workspace, WorkspaceManager, utc_iso

logger = logging.getLogger("fieldledger.web")

HEURISTIC_NOTICE = (
    "Detection is heuristic. It matches column names against configurable rules and never "
    "reads cell values, so it can miss personal data in opaquely named columns and can flag "
    "look-alike names. Every finding needs human review."
)
CSV_NOTES = (
    "CSV files must be UTF-8 with a header row.",
    "Only the comma delimiter is supported. Other delimiters are read as a single column.",
)
REVIEW_NOTICE = (
    "Local review annotations set by a person in this workspace. They are not detection "
    "output and are not part of any FieldLedger report."
)
REPORT_FILES = {
    "json": ("fieldledger_report.json", "application/json"),
    "csv": ("fieldledger_inventory.csv", "text/csv"),
    "md": ("fieldledger_summary.md", "text/markdown"),
}
_DELIMITER_CHARS = (";", "|", "\t")


# ---------------------------------------------------------------------------- engine config
@dataclass(frozen=True)
class Engine:
    registry: ClassRegistry
    ruleset: RuleSet
    root: Path

    @classmethod
    def load(cls, root: Path) -> "Engine":
        root = Path(root).resolve()
        registry = load_class_registry(root / "config" / "classes.yaml")
        ruleset = load_rule_set(root / "config" / "rules.yaml", registry)
        return cls(registry=registry, ruleset=ruleset, root=root)

    @property
    def class_labels(self) -> dict[str, str]:
        return {c.id: c.label for c in self.registry.classes}

    @property
    def benchmark_dir(self) -> Path:
        return self.root / "data" / "synthetic"

    @property
    def labels_path(self) -> Path:
        return self.benchmark_dir / "ground_truth" / "labels.yaml"

    @property
    def bundled_metadata_path(self) -> Path:
        return self.benchmark_dir / "metadata" / "purpose_retention.yaml"

    def benchmark_available(self) -> bool:
        try:
            return (
                self.labels_path.is_file()
                and self.benchmark_dir.is_dir()
                and len(discover_sources(self.benchmark_dir)) > 0
            )
        except FieldLedgerError:
            return False


def _pattern_text(pattern: tuple[str, ...]) -> str:
    return " ".join(pattern)


def rule_dict(rule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "class_id": rule.class_id,
        "match": rule.match,
        "patterns": [_pattern_text(p) for p in rule.patterns],
        "exclude_patterns": [_pattern_text(p) for p in rule.exclude_patterns],
    }


def config_payload(
    engine: Engine, ws_limits: contract.WorkspaceLimits, ingestion: IngestionLimits
) -> dict[str, Any]:
    return {
        "version": __version__,
        "api_version": contract.API_VERSION,
        "taxonomy_status": engine.registry.taxonomy_status,
        "rules_status": engine.ruleset.rules_status,
        "classes": [
            {"id": c.id, "label": c.label, "description": c.description}
            for c in engine.registry.classes
        ],
        "rules": [rule_dict(r) for r in engine.ruleset.rules],
        "limits": {
            "max_file_bytes": ingestion.max_file_bytes,
            "max_rows": ingestion.max_rows,
            "max_columns": ingestion.max_columns,
            "max_line_chars": ingestion.max_line_chars,
            "max_db_bytes": ingestion.max_db_bytes,
            "max_tables": ingestion.max_tables,
            "sqlite_timeout_seconds": ingestion.sqlite_timeout_seconds,
            "max_files": ws_limits.max_files,
            "max_workspace_bytes": ws_limits.max_workspace_bytes,
            "max_files_per_request": ws_limits.max_files_per_request,
            "max_metadata_bytes": ws_limits.max_metadata_bytes,
            "idle_timeout_seconds": ws_limits.idle_timeout_seconds,
            "scan_timeout_seconds": ws_limits.scan_timeout_seconds,
        },
        "supported_suffixes": list(contract.SUPPORTED_SUFFIXES),
        "report_formats": list(contract.REPORT_FORMATS),
        "metadata_origins": list(contract.METADATA_ORIGINS),
        "notices": {"evidence": EVIDENCE_NOTICE, "heuristic": HEURISTIC_NOTICE, "gap": GAP_NOTICE},
        "csv_notes": list(CSV_NOTES),
        "review_statuses": list(contract.REVIEW_STATUSES),
        "benchmark_available": engine.benchmark_available(),
    }


# ---------------------------------------------------------------------------- scan models
@dataclass
class ScanResult:
    generated_at: str
    findings: tuple[Finding, ...]
    ids: dict[tuple, str]
    by_id: dict[str, Finding]
    profiles: dict[str, SourceProfile]
    gap: GapReport | None
    include_declarations: bool
    formats: tuple[str, ...]
    out_dir: Path
    col_classes: dict[tuple, tuple[str, ...]]
    linked: dict[tuple, Any]
    source_counts: dict[str, int]
    column_counts: dict[tuple, int]
    warnings: dict[str, list[str]]
    ambiguous_count: int


class ScanJob:
    def __init__(self, scan_id: str, revision: int, include_declarations: bool, formats: list[str]):
        self.scan_id = scan_id
        self.revision = revision
        self.include_declarations = include_declarations
        self.formats = tuple(formats)
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.state = "queued"
        self.stage: str | None = None
        self.sources_total: int | None = None
        self.sources_done = 0
        self.source_errors: list[dict[str, str]] = []
        self.started_at = utc_iso()
        self.finished_at: str | None = None
        self.error: str | None = None
        self.result: ScanResult | None = None

    def update(self, **kw: Any) -> None:
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def status(self, current_revision: int | None) -> dict[str, Any]:
        with self.lock:
            return {
                "scan_id": self.scan_id,
                "state": self.state,
                "stage": self.stage,
                "sources_total": self.sources_total,
                "sources_done": self.sources_done,
                "source_errors": list(self.source_errors),
                "workspace_revision": self.revision,
                "stale": current_revision is not None and current_revision != self.revision,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
                "formats": list(self.formats),
                "include_declarations": self.include_declarations,
            }

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")


class _Cancelled(Exception):
    pass


def _source_warnings(profile: SourceProfile) -> list[str]:
    if profile.source_type.value != "csv":
        return []
    for table in profile.tables:
        if len(table.columns) == 1 and any(ch in table.columns[0].name for ch in _DELIMITER_CHARS):
            return ["possible_delimiter_mismatch"]
    return []


class Service:
    """Owns the scan job, evaluation jobs and review annotations for one server process."""

    def __init__(
        self,
        engine: Engine,
        manager: WorkspaceManager,
        ingestion: IngestionLimits,
    ) -> None:
        self.engine = engine
        self.manager = manager
        self.ingestion = ingestion
        self._lock = threading.RLock()
        self._job: ScanJob | None = None
        self._thread: threading.Thread | None = None
        self._evals: dict[str, dict[str, Any]] = {}
        manager.on_discard(self._drop_scan)

    def _drop_scan(self) -> None:
        with self._lock:
            self._job = None

    # ------------------------------------------------------------------ scans
    def latest_scan_id(self) -> str | None:
        with self._lock:
            return self._job.scan_id if self._job is not None else None

    def scan_active(self) -> bool:
        with self._lock:
            return self._job is not None and self._job.active

    def start_scan(self, include_declarations: bool, formats: list[str]) -> str:
        ws = self.manager.get()
        if not formats or any(f not in contract.REPORT_FORMATS for f in formats):
            raise ApiError("bad_request", "formats must be a non-empty subset of json, csv, md")
        formats = [f for f in contract.REPORT_FORMATS if f in set(formats)]
        with self._lock:
            if self._job is not None and self._job.active:
                raise ApiError("scan_in_progress", "a scan is already running")
            with ws.lock:
                if include_declarations and ws.metadata is None:
                    raise ApiError("bad_request", "declarations require a metadata file")
                if not ws.files:
                    raise ApiError("bad_request", "upload at least one data file first")
                revision = ws.revision
            old = self._job
            job = ScanJob(uuid.uuid4().hex[:16], revision, include_declarations, formats)
            self._job = job
        if old is not None and old.result is not None:
            from .workspace import rmtree_robust

            rmtree_robust(old.result.out_dir)
        thread = threading.Thread(target=self._run, args=(job, ws), name="fieldledger-scan", daemon=True)
        self._thread = thread
        thread.start()
        logger.info("scan started")
        return job.scan_id

    def job(self, scan_id: str) -> ScanJob:
        with self._lock:
            job = self._job
        if job is None or job.scan_id != scan_id:
            raise ApiError("not_found", "unknown scan")
        return job

    def status(self, scan_id: str) -> dict[str, Any]:
        ws = self.manager.get()
        job = self.job(scan_id)
        return job.status(ws.revision)

    def cancel(self, scan_id: str) -> dict[str, Any]:
        ws = self.manager.get()
        job = self.job(scan_id)
        if job.active:
            job.cancel.set()
            logger.info("scan cancel requested")
        return job.status(ws.revision)

    def result(self, scan_id: str) -> tuple[Workspace, ScanJob, ScanResult]:
        ws = self.manager.get()
        job = self.job(scan_id)
        if job.state != "succeeded" or job.result is None:
            raise ApiError("scan_not_ready", "the scan has not finished successfully")
        return ws, job, job.result

    def _run(self, job: ScanJob, ws: Workspace) -> None:
        engine, started = self.engine, time.monotonic()
        timeout = self.manager.limits.scan_timeout_seconds
        try:
            job.update(state="running", stage="discovering")
            paths = discover_sources(ws.data_dir)
            job.update(sources_total=len(paths), stage="profiling")
            profiles: dict[str, SourceProfile] = {}
            for path in paths:
                if job.cancel.is_set():
                    raise _Cancelled
                if time.monotonic() - started > timeout:
                    raise TimeoutError
                shown_key = path.relative_to(ws.data_dir).as_posix()
                try:
                    profile = profile_source(path, ws.data_dir, self.ingestion)
                    key = source_key_for(path, ws.data_dir)
                    profiles[key] = profile
                except FieldLedgerError as exc:
                    code = contract.source_error_code(exc)
                    with job.lock:
                        job.source_errors.append(
                            {"source_key": shown_key, "code": code, "message": str(exc)}
                        )
                    logger.info("source failed code=%s", code)
                except Exception:  # unexpected: record generically, never echo the message
                    with job.lock:
                        job.source_errors.append(
                            {"source_key": shown_key, "code": "ingest_failed",
                             "message": "the source could not be processed"}
                        )
                    logger.warning("source failed code=ingest_failed")
                with job.lock:
                    job.sources_done += 1

            if job.cancel.is_set():
                raise _Cancelled
            job.update(stage="detecting")
            findings: list[Finding] = []
            for key in sorted(profiles, key=str.casefold):
                findings.extend(detect_candidates(profiles[key], engine.ruleset, source_key=key))
            ids: dict[tuple, str] = {}
            by_id: dict[str, Finding] = {}
            for f in findings:
                fid = contract.finding_id(f.source_key, f.table_name, f.column_name, f.class_id)
                if fid in by_id:
                    raise RuntimeError("duplicate finding id")
                ids[f.identity] = fid
                by_id[fid] = f

            gap: GapReport | None = None
            job.update(stage="linking_metadata")
            with ws.lock:
                has_metadata = ws.metadata is not None
            if has_metadata:
                try:
                    gap = link_metadata(
                        findings,
                        load_metadata(ws.metadata_path),
                        profiles,
                        registry=engine.registry,
                        ruleset=engine.ruleset,
                    )
                except (ConfigError, ValueError, TypeError) as exc:
                    raise RuntimeError(
                        str(exc) if isinstance(exc, ConfigError) else "metadata could not be linked"
                    ) from None

            if job.cancel.is_set():
                raise _Cancelled
            job.update(stage="writing_reports")
            out_dir = ws.reports_dir / job.scan_id
            out_dir.mkdir(parents=True, exist_ok=True)
            write_reports(
                findings, profiles, gap, engine.registry, engine.ruleset, out_dir,
                list(job.formats), include_declarations=job.include_declarations,
            )
            csv_path = out_dir / REPORT_FILES["csv"][0]
            if csv_path.exists():
                original = csv_path.read_bytes().decode("utf-8")
                safe = neutralise_csv_text(original)
                if safe != original:
                    csv_path.write_bytes(safe.encode("utf-8"))

            col_classes: dict[tuple, set[str]] = {}
            source_counts: dict[str, int] = {}
            column_counts: dict[tuple, int] = {}
            for f in findings:
                col_classes.setdefault(f.column_identity, set()).add(f.class_id)
                source_counts[f.source_key] = source_counts.get(f.source_key, 0) + 1
                column_counts[f.column_identity] = column_counts.get(f.column_identity, 0) + 1
            result = ScanResult(
                generated_at=utc_iso(),
                findings=tuple(findings),
                ids=ids,
                by_id=by_id,
                profiles=profiles,
                gap=gap,
                include_declarations=job.include_declarations,
                formats=job.formats,
                out_dir=out_dir,
                col_classes={k: tuple(sorted(v)) for k, v in col_classes.items()},
                linked={lf.column_identity: lf for lf in gap.linked_fields} if gap else {},
                source_counts=source_counts,
                column_counts=column_counts,
                warnings={k: _source_warnings(p) for k, p in profiles.items()},
                ambiguous_count=len(ambiguous_columns(findings)),
            )
            with job.lock:
                job.result = result
                job.state = "succeeded"
                job.stage = None
                job.finished_at = utc_iso()
            logger.info("scan succeeded sources=%d findings=%d errors=%d",
                        len(profiles), len(findings), len(job.source_errors))
        except _Cancelled:
            job.update(state="cancelled", stage=None, finished_at=utc_iso())
            logger.info("scan cancelled")
        except TimeoutError:
            job.update(state="failed", stage=None, finished_at=utc_iso(),
                       error="the scan exceeded its time limit")
            logger.warning("scan timed out")
        except Exception as exc:
            safe = str(exc) if isinstance(exc, (RuntimeError, ConfigError)) and str(exc) else \
                "the scan failed unexpectedly"
            job.update(state="failed", stage=None, finished_at=utc_iso(), error=safe)
            logger.warning("scan failed type=%s", type(exc).__name__)

    # ------------------------------------------------------------------ results
    def _review_status(self, ws: Workspace, fid: str) -> str:
        entry = ws.reviews.get(fid)
        return entry["status"] if entry else "unreviewed"

    def _item(self, ws: Workspace, r: ScanResult, f: Finding) -> dict[str, Any]:
        fid = r.ids[f.identity]
        classes = r.col_classes[f.column_identity]
        linked = r.linked.get(f.column_identity)
        return {
            "id": fid,
            "source_key": f.source_key,
            "table": f.table_name,
            "column": f.column_name,
            "position": f.column_position,
            "class_id": f.class_id,
            "class_label": self.engine.class_labels.get(f.class_id, f.class_id),
            "rule_ids": list(f.rule_ids),
            "evidence_kind": f.evidence_kind,
            "ambiguous": len(classes) > 1,
            "other_class_ids": [c for c in classes if c != f.class_id],
            "metadata_status": linked.status.value if linked else None,
            "has_purpose": linked.has_purpose if linked else None,
            "has_retention": linked.has_retention if linked else None,
            "review_status": self._review_status(ws, fid),
        }

    def summary(self, scan_id: str) -> dict[str, Any]:
        ws, job, r = self.result(scan_id)
        by_class = {c.id: 0 for c in self.engine.registry.classes}
        for f in r.findings:
            by_class[f.class_id] = by_class.get(f.class_id, 0) + 1
        metadata = None
        by_class_metadata: dict[str, dict[str, int]] | None = None
        if r.gap is not None:
            metadata = r.gap.summary.to_dict()
            by_class_metadata = {
                c.id: {st: 0 for st in contract.METADATA_STATUSES} for c in self.engine.registry.classes
            }
            for f in r.findings:  # one finding per column and class, as in the findings list
                linked = r.linked.get(f.column_identity)
                if linked is not None:
                    by_class_metadata[f.class_id][linked.status.value] += 1
        counts = {s: 0 for s in contract.REVIEW_STATUSES}
        with ws.lock:
            for fid in r.by_id:
                counts[self._review_status(ws, fid)] += 1
        return {
            "generated_at": r.generated_at,
            "sources_scanned": len(r.profiles),
            "sources_failed": len(job.source_errors),
            "total_findings": len(r.findings),
            "by_class": by_class,
            "by_source": {k: r.source_counts.get(k, 0) for k in sorted(r.profiles, key=str.casefold)},
            "ambiguous_columns": r.ambiguous_count,
            "metadata": metadata,
            "by_class_metadata": by_class_metadata,
            "review_counts": counts,
            "taxonomy_status": self.engine.registry.taxonomy_status,
            "rules_status": self.engine.ruleset.rules_status,
            "include_declarations": r.include_declarations,
            "formats": list(r.formats),
            "notices": {"evidence": EVIDENCE_NOTICE, "heuristic": HEURISTIC_NOTICE},
        }

    def sources(self, scan_id: str) -> dict[str, Any]:
        _ws, _job, r = self.result(scan_id)
        out = []
        for key in sorted(r.profiles, key=str.casefold):
            data = r.profiles[key].to_dict()
            tables = []
            for t in data["tables"]:
                cols = [
                    {**c, "finding_count": r.column_counts.get((key, t["name"], c["name"]), 0)}
                    for c in t["columns"]
                ]
                tables.append({"name": t["name"], "row_count": t["row_count"], "columns": cols})
            out.append({
                "source_key": key,
                "source_type": data["source_type"],
                "type_basis": data["type_basis"],
                "finding_count": r.source_counts.get(key, 0),
                "warnings": r.warnings.get(key, []),
                "tables": tables,
            })
        return {"sources": out}

    def findings(self, scan_id: str, q: dict[str, Any]) -> dict[str, Any]:
        ws, _job, r = self.result(scan_id)
        with ws.lock:
            items = [self._item(ws, r, f) for f in r.findings]
        needle = (q.get("q") or "").casefold()
        if needle:
            items = [i for i in items if needle in i["source_key"].casefold()
                     or needle in i["table"].casefold() or needle in i["column"].casefold()]
        for field_name, key in (("class", "class_id"), ("source", "source_key"),
                                ("metadata_status", "metadata_status"),
                                ("review_status", "review_status")):
            wanted = q.get(field_name)
            if wanted:
                items = [i for i in items if i[key] in wanted]
        if q.get("ambiguous") is not None:
            items = [i for i in items if i["ambiguous"] is q["ambiguous"]]
        tie = lambda i: (i["source_key"].casefold(), i["table"].casefold(), i["position"], i["class_id"])
        items.sort(key=tie)
        primary = {
            "source": lambda i: i["source_key"].casefold(),
            "table": lambda i: i["table"].casefold(),
            "column": lambda i: i["column"].casefold(),
            "class": lambda i: i["class_id"],
            "position": lambda i: i["position"],
        }[q["sort"]]
        items.sort(key=primary, reverse=q["order"] == "desc")
        page, size = q["page"], q["page_size"]
        start = (page - 1) * size
        return {"items": items[start:start + size], "total": len(items), "page": page, "page_size": size}

    def finding_detail(self, scan_id: str, finding_id: str) -> dict[str, Any]:
        ws, _job, r = self.result(scan_id)
        f = r.by_id.get(finding_id)
        if f is None:
            raise ApiError("not_found", "unknown finding")
        with ws.lock:
            item = self._item(ws, r, f)
            review = ws.reviews.get(finding_id)
        rules_by_id = {rule.id: rule for rule in self.engine.ruleset.rules}
        rules = [rule_dict(rules_by_id[rid]) for rid in f.rule_ids if rid in rules_by_id]

        # Parity rule (contract section 9): name a pattern only for rule ids that the
        # detector's own matcher (explain_column) reproduces.
        matches = explain_column(f.column_name, self.engine.ruleset)
        by_rule = {m.rule_id: m for m in matches if not m.suppressed}
        explained = [
            {"rule_id": rid, "pattern": _pattern_text(by_rule[rid].matched_patterns[0])}
            for rid in f.rule_ids
            if rid in by_rule and by_rule[rid].matched_patterns
        ]
        if len(explained) == len(f.rule_ids):
            explanation: dict[str, Any] = {"kind": "matched_pattern", "matches": explained}
        else:
            explanation = {"kind": "rule_patterns_only"}
        suppressed = [
            {"rule_id": m.rule_id, "class_id": m.class_id,
             "excluded_by": [_pattern_text(p) for p in m.excluded_by]}
            for m in matches if m.suppressed
        ]

        col_profile = None
        profile = r.profiles.get(f.source_key)
        if profile is not None:
            for table in profile.tables:
                if table.name != f.table_name:
                    continue
                for col in table.columns:
                    if col.name == f.column_name:
                        col_profile = {
                            "inferred_type": col.inferred_type.value,
                            "declared_type": col.declared_type,
                            "non_null_count": col.non_null_count,
                            "null_count": col.null_count,
                            "table_row_count": table.row_count,
                        }
        siblings = [
            {"id": r.ids[o.identity], "class_id": o.class_id}
            for o in r.findings
            if o.column_identity == f.column_identity and o.identity != f.identity
        ]
        detail = {
            **item,
            "rules": rules,
            "column_tokens": list(tokenize_name(f.column_name)),
            "explanation": explanation,
            "suppressed_rules": suppressed,
            "column_profile": col_profile,
            "siblings": siblings,
            "review": None if review is None else {"status": review["status"], "updated_at": review["updated_at"]},
            "notice": EVIDENCE_NOTICE,
        }
        if r.include_declarations:
            linked = r.linked.get(f.column_identity)
            entry = linked.entry if linked else None
            detail["declaration"] = {
                "purpose": entry.purpose if entry else None,
                "retention": entry.retention if entry else None,
            }
        return detail

    def set_review(self, scan_id: str, finding_id: str, status: str) -> dict[str, Any]:
        ws, _job, r = self.result(scan_id)
        if status not in contract.REVIEW_STATUSES:
            raise ApiError("bad_request", "unknown review status")
        f = r.by_id.get(finding_id)
        if f is None:
            raise ApiError("not_found", "unknown finding")
        with ws.lock:
            if status == "unreviewed":
                ws.reviews.pop(finding_id, None)
            else:
                ws.reviews[finding_id] = {
                    "finding_id": finding_id, "source_key": f.source_key, "table": f.table_name,
                    "column": f.column_name, "class_id": f.class_id, "status": status,
                    "updated_at": utc_iso(),
                }
            ws.save_reviews()
            return {"id": finding_id, "review_status": status, "review_count": len(ws.reviews)}

    def reviews_export(self) -> bytes:
        ws = self.manager.get()
        with self._lock:
            job = self._job
        current_ids = set(job.result.by_id) if job and job.result else set()
        with ws.lock:
            entries = [
                {**e, "current": fid in current_ids}
                for fid, e in sorted(ws.reviews.items(), key=lambda kv: (kv[1]["source_key"], kv[1]["table"], kv[1]["column"], kv[1]["class_id"]))
            ]
        doc = {"notice": REVIEW_NOTICE, "generated_at": utc_iso(), "count": len(entries), "annotations": entries}
        return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    def reset_reviews(self) -> int:
        ws = self.manager.get()
        with ws.lock:
            n = len(ws.reviews)
            ws.reviews.clear()
            ws.save_reviews()
        return n

    def metadata(self, scan_id: str) -> dict[str, Any]:
        _ws, _job, r = self.result(scan_id)
        if r.gap is None:
            return {"available": False}
        data = r.gap.to_dict(include_declarations=r.include_declarations)
        for row in data["linked_fields"]:
            row["id_by_class"] = {
                cid: r.ids.get((row["source_key"], row["table"], row["column"], cid))
                for cid in row["class_ids"]
            }
        return {"available": True, **data}

    def export(self, scan_id: str, fmt: str) -> tuple[Path, str, str]:
        _ws, _job, r = self.result(scan_id)
        if fmt not in contract.REPORT_FORMATS:
            raise ApiError("bad_request", "unknown export format")
        if fmt not in r.formats:
            raise ApiError("format_not_generated", "this format was not requested for this scan")
        name, media = REPORT_FILES[fmt]
        path = r.out_dir / name
        if not path.is_file():
            raise ApiError("not_found", "the report file is no longer available")
        return path, media, name

    # ------------------------------------------------------------------ evaluation
    def start_evaluation(self, dataset: str) -> str:
        if dataset != "bundled_synthetic":
            raise ApiError("bad_request", "unknown dataset")
        if not self.engine.benchmark_available():
            raise ApiError("benchmark_unavailable", "the bundled synthetic benchmark was not found")
        eid = uuid.uuid4().hex[:16]
        with self._lock:
            if any(e["state"] in ("queued", "running") for e in self._evals.values()):
                raise ApiError("scan_in_progress", "an evaluation is already running")
            self._evals[eid] = {"evaluation_id": eid, "state": "queued", "result": None,
                                "error": None, "started_at": utc_iso(), "finished_at": None}
        threading.Thread(target=self._run_eval, args=(eid,), name="fieldledger-eval", daemon=True).start()
        return eid

    def evaluation(self, eid: str) -> dict[str, Any]:
        with self._lock:
            e = self._evals.get(eid)
            if e is None:
                raise ApiError("not_found", "unknown evaluation")
            return dict(e)

    def _run_eval(self, eid: str) -> None:
        eng = self.engine
        with self._lock:
            self._evals[eid]["state"] = "running"
        try:
            base = eng.benchmark_dir.resolve()
            profiles: dict[str, SourceProfile] = {}
            findings: list[Finding] = []
            for path in discover_sources(base):
                profile = profile_source(path, base, self.ingestion)
                key = source_key_for(path, base)
                profiles[key] = profile
                findings.extend(detect_candidates(profile, eng.ruleset, source_key=key))
            labels = load_labels(eng.labels_path, eng.registry)
            report = evaluate(findings, labels, profiles, registry=eng.registry, ruleset=eng.ruleset)
            data = report.to_dict()
            tp = sum(c["true_positives"] for c in data["classes"])
            fp = sum(c["false_positives"] for c in data["classes"])
            fn = sum(c["false_negatives"] for c in data["classes"])
            result = {
                **data,
                "scope": "synthetic_benchmark",
                "generated_at": utc_iso(),
                "fieldledger_version": __version__,
                "micro": {
                    "tp": tp, "fp": fp, "fn": fn,
                    "predicted": tp + fp, "labelled_positive": tp + fn,
                    "precision": tp / (tp + fp) if tp + fp else None,
                    "recall": tp / (tp + fn) if tp + fn else None,
                    "derived": True,
                },
            }
            with self._lock:
                self._evals[eid].update(state="succeeded", result=result, finished_at=utc_iso())
            logger.info("evaluation succeeded")
        except Exception as exc:
            safe = str(exc) if isinstance(exc, FieldLedgerError) else "the evaluation failed unexpectedly"
            with self._lock:
                self._evals[eid].update(state="failed", error=safe, finished_at=utc_iso())
            logger.warning("evaluation failed type=%s", type(exc).__name__)
