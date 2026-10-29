"""Purpose/retention metadata linking and review-gap reporting (Milestone 5).

`link_metadata` joins the candidate findings of a scan to DECLARED purpose and retention
metadata and reports where a declaration is missing. The result is technical evidence for
human review. A missing declaration is a REVIEW GAP in the supplied metadata. It is not a
legal finding, not proof of non-compliance, and a complete declaration is not evidence of
compliance either. Declarations are taken exactly as supplied and are never assessed.

Ground truth is not used here. This module never imports or reads the labels or the
evaluation code; it only sees what a real user has: findings, profiles and a metadata file.

Metadata file format (team-defined; the official PS-019 text does not prescribe one):

    metadata_origin: team_authored_synthetic | organizer_provided | user_provided
    entries:                              # may be an empty list
      - source_key: customers.csv         # same key the detector used (relative POSIX path)
        table: customers
        column: email
        purpose: "Order fulfilment"       # optional
        retention: "24 months"            # optional

Declarations: an absent key, ``null``, an empty string or a whitespace-only string all mean
"not declared". Other text is kept after collapsing runs of whitespace (including line
breaks) to one space. A non-string value, a control character, or text over
`MAX_DECLARATION_CHARS` is a ConfigError. Placeholder text such as "TBD" or "N/A" counts as
declared: FieldLedger does not judge the meaning of a declaration.

Identity and matching:
- A column is identified by exactly ``(source_key, table, column)``, the same triple as
  `Finding.column_identity`. Two files with the same name in different directories, or the
  same column name in different tables, are different columns and are never merged.
- Matching is EXACT and case-sensitive on all three parts, so results are deterministic.
  LIMITATION: SQLite treats table and column names case-insensitively, but FieldLedger
  compares the strings the profiler reports. An entry that differs from a scanned column
  only by letter case therefore does not match. The column gets status NO_ENTRY and the
  entry is listed as unmatched with ``case_variant_exists`` set, as a hint for the
  reviewer. FieldLedger never matches or merges such entries automatically.
- Metadata is class-agnostic, so a column with findings for several classes is ONE row with
  every class id listed and ``ambiguous`` set.

Entries that do not belong to a flagged column are reported, not rejected:
- orphan entry: the column exists in the scan but has no finding (declared, not flagged);
- unmatched entry: the source, table or column is not in the scan (for example a stale
  declaration for a dropped column).

Privacy: reports hold identifiers and booleans only. They never contain cell values or
absolute paths. Declaration text is included only when ``include_declarations=True`` is
passed explicitly. Declaration text is also hidden from ``repr``. Logging is limited to
counts. Error messages carry a file name, an index and a fixed description, never names or
text from the file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from .classes import ClassRegistry
from .errors import ConfigError
from .findings import EVIDENCE_NOTICE, Finding, validate_source_key
from .profiles import SourceProfile
from .rules import RuleSet
from .strict_yaml import load_strict_yaml_mapping

logger = logging.getLogger(__name__)

MAX_METADATA_ENTRIES = 20_000
MAX_NAME_CHARS = 1_024
MAX_DECLARATION_CHARS = 1_024
METADATA_ORIGINS = ("team_authored_synthetic", "organizer_provided", "user_provided")
GAP_NOTICE = (
    "A missing purpose or retention declaration is a review gap in the supplied metadata. "
    "It is not evidence of a legal violation or non-compliance, and a complete declaration "
    "is not evidence of compliance. Declarations are reported as supplied and are not "
    "assessed. Human review is required."
)

_TOP_KEYS = {"metadata_origin", "entries"}
_ENTRY_REQUIRED = {"source_key", "table", "column"}
_ENTRY_KEYS = _ENTRY_REQUIRED | {"purpose", "retention"}

ColumnIdentity = tuple[str, str, str]  # (source_key, table, column)


class DeclarationStatus(str, Enum):
    COMPLETE = "complete"
    MISSING_PURPOSE = "missing_purpose"
    MISSING_RETENTION = "missing_retention"
    MISSING_BOTH = "missing_both"  # an entry exists but declares neither
    NO_ENTRY = "no_entry"  # no entry for this column at all


class UnmatchedReason(str, Enum):
    SOURCE_NOT_SCANNED = "source_not_scanned"
    TABLE_NOT_FOUND = "table_not_found"
    COLUMN_NOT_FOUND = "column_not_found"


def _normalise_declaration(value: object, label: str) -> str | None:
    """Return the cleaned declaration, or None if it is not declared. Raises ValueError."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string or null")
    text = " ".join(value.split())
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError(f"{label} contains control characters")
    if len(text) > MAX_DECLARATION_CHARS:
        raise ValueError(f"{label} is too long")
    return text or None


def _check_bool(include_declarations: object) -> bool:
    if not isinstance(include_declarations, bool):
        raise TypeError("include_declarations must be a bool")
    return include_declarations


@dataclass(frozen=True)
class MetadataEntry:
    source_key: str
    table_name: str
    column_name: str
    purpose: str | None = field(default=None, repr=False)
    retention: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        try:
            validate_source_key(self.source_key)
        except ValueError:
            raise ValueError("source_key is invalid") from None
        for attr, label in (("table_name", "table"), ("column_name", "column")):
            value = getattr(self, attr)
            if not isinstance(value, str) or not 0 < len(value) <= MAX_NAME_CHARS:
                raise ValueError(
                    f"{label} must be a non-empty string of at most {MAX_NAME_CHARS} characters"
                )
        object.__setattr__(self, "purpose", _normalise_declaration(self.purpose, "purpose"))
        object.__setattr__(self, "retention", _normalise_declaration(self.retention, "retention"))

    @property
    def column_identity(self) -> ColumnIdentity:
        return (self.source_key, self.table_name, self.column_name)

    @property
    def has_purpose(self) -> bool:
        return self.purpose is not None

    @property
    def has_retention(self) -> bool:
        return self.retention is not None

    def to_dict(self, *, include_declarations: bool = False) -> dict[str, Any]:
        include = _check_bool(include_declarations)
        out: dict[str, Any] = {
            "source_key": self.source_key,
            "table": self.table_name,
            "column": self.column_name,
            "has_purpose": self.has_purpose,
            "has_retention": self.has_retention,
        }
        if include:
            out["purpose"] = self.purpose
            out["retention"] = self.retention
        return out


@dataclass(frozen=True)
class MetadataSet:
    metadata_origin: str
    entries: tuple[MetadataEntry, ...]  # sorted by column identity

    def __post_init__(self) -> None:
        if self.metadata_origin not in METADATA_ORIGINS:
            raise ValueError("unsupported metadata_origin")
        entries = tuple(self.entries)
        if not all(isinstance(e, MetadataEntry) for e in entries):
            raise ValueError("entries must be MetadataEntry instances")
        identities = [e.column_identity for e in entries]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate metadata entry")
        object.__setattr__(self, "entries", tuple(sorted(entries, key=lambda e: e.column_identity)))


def load_metadata(path: str | Path) -> MetadataSet:
    """Load and validate a metadata file. Raises ConfigError on any problem."""
    name = Path(path).name
    data = load_strict_yaml_mapping(path)

    if set(data) - _TOP_KEYS:
        raise ConfigError(f"{name}: unknown top-level keys (allowed: {', '.join(sorted(_TOP_KEYS))})")
    origin = data.get("metadata_origin")
    if origin not in METADATA_ORIGINS:
        raise ConfigError(f"{name}: metadata_origin must be one of {list(METADATA_ORIGINS)}")

    raw = data.get("entries")
    if not isinstance(raw, list):
        raise ConfigError(f"{name}: entries must be a list (use an empty list for no metadata)")
    if len(raw) > MAX_METADATA_ENTRIES:
        raise ConfigError(f"{name}: too many metadata entries")

    entries: list[MetadataEntry] = []
    seen: set[ColumnIdentity] = set()
    for i, item in enumerate(raw):
        where = f"entries[{i}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{name}: {where} must be a mapping")
        if set(item) - _ENTRY_KEYS:
            raise ConfigError(
                f"{name}: {where} has unknown keys (allowed: {', '.join(sorted(_ENTRY_KEYS))})"
            )
        missing = _ENTRY_REQUIRED - set(item)
        if missing:
            raise ConfigError(f"{name}: {where} is missing required keys: {sorted(missing)}")
        try:
            entry = MetadataEntry(
                source_key=item["source_key"],
                table_name=item["table"],
                column_name=item["column"],
                purpose=item.get("purpose"),
                retention=item.get("retention"),
            )
        except ValueError as exc:  # messages are fixed descriptions, never values
            raise ConfigError(f"{name}: {where}: {exc}") from None
        if entry.column_identity in seen:
            raise ConfigError(f"{name}: duplicate metadata entry at {where}")
        seen.add(entry.column_identity)
        entries.append(entry)

    return MetadataSet(metadata_origin=origin, entries=tuple(entries))


def _status_for(entry: MetadataEntry | None) -> DeclarationStatus:
    if entry is None:
        return DeclarationStatus.NO_ENTRY
    if entry.has_purpose and entry.has_retention:
        return DeclarationStatus.COMPLETE
    if entry.has_retention:
        return DeclarationStatus.MISSING_PURPOSE
    if entry.has_purpose:
        return DeclarationStatus.MISSING_RETENTION
    return DeclarationStatus.MISSING_BOTH


@dataclass(frozen=True)
class LinkedField:
    """One flagged column joined to its declaration (if any)."""

    source_key: str
    table_name: str
    column_name: str
    column_position: int
    class_ids: tuple[str, ...]  # sorted, at least one
    status: DeclarationStatus
    entry: MetadataEntry | None = field(default=None, repr=False)

    @property
    def column_identity(self) -> ColumnIdentity:
        return (self.source_key, self.table_name, self.column_name)

    @property
    def ambiguous(self) -> bool:
        return len(self.class_ids) > 1

    @property
    def has_purpose(self) -> bool:
        return self.entry is not None and self.entry.has_purpose

    @property
    def has_retention(self) -> bool:
        return self.entry is not None and self.entry.has_retention

    def to_dict(self, *, include_declarations: bool = False) -> dict[str, Any]:
        include = _check_bool(include_declarations)
        out: dict[str, Any] = {
            "source_key": self.source_key,
            "table": self.table_name,
            "column": self.column_name,
            "position": self.column_position,
            "class_ids": list(self.class_ids),
            "ambiguous": self.ambiguous,
            "status": self.status.value,
            "has_purpose": self.has_purpose,
            "has_retention": self.has_retention,
        }
        if include:
            out["purpose"] = self.entry.purpose if self.entry is not None else None
            out["retention"] = self.entry.retention if self.entry is not None else None
        return out


@dataclass(frozen=True)
class UnmatchedEntry:
    """A metadata entry whose column is not in the scan (never silently dropped)."""

    entry: MetadataEntry = field(repr=False)
    reason: UnmatchedReason
    case_variant_exists: bool  # a scanned column differs from this entry only by letter case

    def to_dict(self, *, include_declarations: bool = False) -> dict[str, Any]:
        out = self.entry.to_dict(include_declarations=include_declarations)
        out["reason"] = self.reason.value
        out["case_variant_exists"] = self.case_variant_exists
        return out


@dataclass(frozen=True)
class GapSummary:
    flagged_columns: int
    ambiguous_columns: int
    by_status: tuple[tuple[str, int], ...]  # every status, enum order
    metadata_entries: int
    orphan_entries: int
    unmatched_entries: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "flagged_columns": self.flagged_columns,
            "ambiguous_columns": self.ambiguous_columns,
            "by_status": {status: count for status, count in self.by_status},
            "metadata_entries": self.metadata_entries,
            "orphan_entries": self.orphan_entries,
            "unmatched_entries": self.unmatched_entries,
        }


@dataclass(frozen=True)
class GapReport:
    metadata_origin: str
    taxonomy_status: str
    rules_status: str
    summary: GapSummary
    linked_fields: tuple[LinkedField, ...]  # one per flagged column, stable order
    orphan_entries: tuple[MetadataEntry, ...]
    unmatched_entries: tuple[UnmatchedEntry, ...]

    @property
    def gap_fields(self) -> tuple[LinkedField, ...]:
        """Flagged columns whose declaration is not complete (review gaps)."""
        return tuple(r for r in self.linked_fields if r.status is not DeclarationStatus.COMPLETE)

    def to_dict(self, *, include_declarations: bool = False) -> dict[str, Any]:
        include = _check_bool(include_declarations)
        return {
            "technical_evidence_notice": EVIDENCE_NOTICE,
            "review_gap_notice": GAP_NOTICE,
            "declarations_included": include,
            "metadata_origin": self.metadata_origin,
            "taxonomy_status": self.taxonomy_status,
            "rules_status": self.rules_status,
            "summary": self.summary.to_dict(),
            "linked_fields": [r.to_dict(include_declarations=include) for r in self.linked_fields],
            "orphan_entries": [e.to_dict(include_declarations=include) for e in self.orphan_entries],
            "unmatched_entries": [
                u.to_dict(include_declarations=include) for u in self.unmatched_entries
            ],
        }


def link_metadata(
    findings: Iterable[Finding],
    metadata: MetadataSet,
    profiles_by_key: Mapping[str, SourceProfile],
    *,
    registry: ClassRegistry,
    ruleset: RuleSet,
) -> GapReport:
    """Join `findings` to `metadata` and report purpose/retention gaps. See the module docstring.

    `profiles_by_key` holds every scanned source under the key the detector used. Findings
    must refer to columns in it (ValueError otherwise: both come from the same scan, so a
    mismatch is a caller bug). Duplicate finding identities, unknown classes and non-Finding
    items raise ValueError or TypeError. Metadata problems never abort the link: entries that
    do not belong to a flagged column are returned as orphan or unmatched entries.
    """
    if not isinstance(metadata, MetadataSet):
        raise TypeError("metadata must be a MetadataSet")
    class_set = set(registry.ids())

    scanned: set[ColumnIdentity] = set()
    scanned_tables: set[tuple[str, str]] = set()
    scanned_folded: set[ColumnIdentity] = set()
    for key, profile in profiles_by_key.items():
        validate_source_key(key)
        for table in profile.tables:
            scanned_tables.add((key, table.name))
            for column in table.columns:
                scanned.add((key, table.name, column.name))
                scanned_folded.add((key.casefold(), table.name.casefold(), column.name.casefold()))

    grouped: dict[ColumnIdentity, tuple[int, set[str]]] = {}
    seen: set[tuple[str, str, str, str]] = set()
    for f in findings:
        if not isinstance(f, Finding):
            raise TypeError("findings must be Finding instances")
        if f.identity in seen:
            raise ValueError("duplicate finding identity")
        seen.add(f.identity)
        if f.class_id not in class_set:
            raise ValueError("finding refers to a class that is not in the registry")
        if f.column_identity not in scanned:
            raise ValueError("finding refers to a column that is not in its profiled source")
        position, classes = grouped.get(f.column_identity, (f.column_position, set()))
        classes.add(f.class_id)
        grouped[f.column_identity] = (min(position, f.column_position), classes)

    by_identity = {e.column_identity: e for e in metadata.entries}

    rows: list[LinkedField] = []
    for identity, (position, classes) in grouped.items():
        entry = by_identity.get(identity)
        rows.append(
            LinkedField(
                source_key=identity[0],
                table_name=identity[1],
                column_name=identity[2],
                column_position=position,
                class_ids=tuple(sorted(classes)),
                status=_status_for(entry),
                entry=entry,
            )
        )
    rows.sort(key=lambda r: (r.source_key, r.table_name, r.column_position, r.column_name))

    orphans = tuple(
        e for e in metadata.entries if e.column_identity in scanned and e.column_identity not in grouped
    )

    unmatched: list[UnmatchedEntry] = []
    for e in metadata.entries:
        if e.column_identity in scanned:
            continue
        if e.source_key not in profiles_by_key:
            reason = UnmatchedReason.SOURCE_NOT_SCANNED
        elif (e.source_key, e.table_name) not in scanned_tables:
            reason = UnmatchedReason.TABLE_NOT_FOUND
        else:
            reason = UnmatchedReason.COLUMN_NOT_FOUND
        folded = (e.source_key.casefold(), e.table_name.casefold(), e.column_name.casefold())
        unmatched.append(UnmatchedEntry(entry=e, reason=reason, case_variant_exists=folded in scanned_folded))

    counts = {status: 0 for status in DeclarationStatus}
    for row in rows:
        counts[row.status] += 1
    summary = GapSummary(
        flagged_columns=len(rows),
        ambiguous_columns=sum(1 for r in rows if r.ambiguous),
        by_status=tuple((status.value, counts[status]) for status in DeclarationStatus),
        metadata_entries=len(metadata.entries),
        orphan_entries=len(orphans),
        unmatched_entries=len(unmatched),
    )
    logger.debug(
        "metadata link: flagged=%d entries=%d orphans=%d unmatched=%d",
        summary.flagged_columns, summary.metadata_entries, summary.orphan_entries,
        summary.unmatched_entries,
    )
    return GapReport(
        metadata_origin=metadata.metadata_origin,
        taxonomy_status=registry.taxonomy_status,
        rules_status=ruleset.rules_status,
        summary=summary,
        linked_fields=tuple(rows),
        orphan_entries=orphans,
        unmatched_entries=tuple(unmatched),
    )
