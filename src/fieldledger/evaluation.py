"""Per-class precision and recall against a manually labelled synthetic set (Milestone 4).

Labels file (YAML, read with the strict loader):

    label_origin: team_authored_synthetic | organizer_provided
    sources:
      - source_key: customers.csv          # same key the detector used (relative POSIX path)
        positives:                         # columns that ARE personal data, with their class
          - {table: customers, column: some_column, class_id: <registry class id>}
        not_personal_data:                 # OPTIONAL explicit negatives
          - {table: customers, column: another_column}

Scoring unit: the exact identity ``(source_key, table, column, class_id)``, identical to
`Finding.identity`. Within a labelled source, every column/class pair that is not listed
as a positive is a negative.

`not_personal_data` is optional. When a source lists it, the labelling of that source must be
COMPLETE: every profiled column has to appear exactly once, either as a positive or as an
explicit "not personal data" entry, otherwise `evaluate` raises ConfigError (an unlabelled
column is never silently treated as negative). Sources that omit the key keep the implicit
"everything not listed is negative" behaviour.

    TP = predicted and labelled     FP = predicted, not labelled     FN = labelled, not predicted
    precision = TP / (TP + FP)      recall = TP / (TP + FN)

A ratio whose denominator is 0 is `None` (undefined). It is never reported as 0 or 1, and no
aggregate or F-score is computed, because averaging would hide undefined classes.

Results describe THIS labelled set only. They are not general accuracy. The report carries
the taxonomy status and rules status so provisional inputs are never presented as official.
Error messages give the file name and indices only, never source keys, tables or columns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .classes import ClassRegistry
from .detector import AmbiguousColumn, ambiguous_columns
from .errors import ConfigError
from .findings import Finding, validate_source_key
from .profiles import SourceProfile
from .rules import RuleSet
from .strict_yaml import load_strict_yaml_mapping

logger = logging.getLogger(__name__)

MAX_LABEL_SOURCES = 1_000
MAX_LABELS_PER_SOURCE = 10_000
MAX_NAME_CHARS = 1_024
LABEL_ORIGINS = ("team_authored_synthetic", "organizer_provided")
SCOPE_NOTICES = {
    "team_authored_synthetic": (
        "Results describe performance on this team-authored synthetic labelled set only. "
        "They are not a measure of general accuracy."
    ),
    "organizer_provided": (
        "Results describe performance on this organizer-provided labelled set only. "
        "They are not a measure of general accuracy."
    ),
}

_TOP_KEYS = {"label_origin", "sources"}
_SOURCE_REQUIRED = {"source_key", "positives"}
_SOURCE_KEYS = _SOURCE_REQUIRED | {"not_personal_data"}
_POSITIVE_KEYS = {"table", "column", "class_id"}
_NEGATIVE_KEYS = {"table", "column"}

Positive = tuple[str, str, str]  # (table, column, class_id)


@dataclass(frozen=True)
class LabeledSource:
    source_key: str
    positives: tuple[Positive, ...]  # sorted
    # Explicit negatives, sorted; None means the source did not list any (implicit negatives).
    not_personal_data: tuple[tuple[str, str], ...] | None = None


@dataclass(frozen=True)
class LabelSet:
    label_origin: str
    sources: tuple[LabeledSource, ...]


def _name_field(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_NAME_CHARS


def load_labels(path: str | Path, registry: ClassRegistry) -> LabelSet:
    """Load and validate a labels file against `registry`. Raises ConfigError on any problem."""
    name = Path(path).name
    data = load_strict_yaml_mapping(path)

    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"{name}: unknown top-level keys: {sorted(unknown)}")
    origin = data.get("label_origin")
    if origin not in LABEL_ORIGINS:
        raise ConfigError(f"{name}: label_origin must be one of {list(LABEL_ORIGINS)}")

    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ConfigError(f"{name}: sources must be a non-empty list")
    if len(raw_sources) > MAX_LABEL_SOURCES:
        raise ConfigError(f"{name}: too many labelled sources")

    class_ids = registry.ids()
    seen_keys: set[str] = set()
    sources: list[LabeledSource] = []
    for i, item in enumerate(raw_sources):
        where = f"sources[{i}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{name}: {where} must be a mapping")
        extra = set(item) - _SOURCE_KEYS
        if extra:
            raise ConfigError(f"{name}: {where} has unknown keys: {sorted(extra)}")
        missing = _SOURCE_REQUIRED - set(item)
        if missing:
            raise ConfigError(f"{name}: {where} is missing keys: {sorted(missing)}")
        try:
            key = validate_source_key(item["source_key"])
        except ValueError:
            raise ConfigError(f"{name}: {where}.source_key is invalid") from None
        if key in seen_keys:
            raise ConfigError(f"{name}: duplicate source_key at {where}")
        seen_keys.add(key)

        raw_pos = item["positives"]
        if not isinstance(raw_pos, list):
            raise ConfigError(f"{name}: {where}.positives must be a list")
        if len(raw_pos) > MAX_LABELS_PER_SOURCE:
            raise ConfigError(f"{name}: {where}.positives has too many entries")
        positives: set[Positive] = set()
        for j, pos in enumerate(raw_pos):
            pwhere = f"{where}.positives[{j}]"
            if not isinstance(pos, dict):
                raise ConfigError(f"{name}: {pwhere} must be a mapping")
            extra = set(pos) - _POSITIVE_KEYS
            if extra:
                raise ConfigError(f"{name}: {pwhere} has unknown keys: {sorted(extra)}")
            missing = _POSITIVE_KEYS - set(pos)
            if missing:
                raise ConfigError(f"{name}: {pwhere} is missing keys: {sorted(missing)}")
            if not _name_field(pos["table"]):
                raise ConfigError(f"{name}: {pwhere}.table must be a non-empty string")
            if not _name_field(pos["column"]):
                raise ConfigError(f"{name}: {pwhere}.column must be a non-empty string")
            if not isinstance(pos["class_id"], str) or pos["class_id"] not in class_ids:
                raise ConfigError(f"{name}: {pwhere}.class_id is not in the class registry")
            entry = (pos["table"], pos["column"], pos["class_id"])
            if entry in positives:
                raise ConfigError(f"{name}: duplicate label at {pwhere}")
            positives.add(entry)

        negatives: tuple[tuple[str, str], ...] | None = None
        if "not_personal_data" in item:
            raw_neg = item["not_personal_data"]
            if not isinstance(raw_neg, list):
                raise ConfigError(f"{name}: {where}.not_personal_data must be a list")
            if len(raw_neg) > MAX_LABELS_PER_SOURCE:
                raise ConfigError(f"{name}: {where}.not_personal_data has too many entries")
            positive_columns = {(t, c) for t, c, _ in positives}
            neg_set: set[tuple[str, str]] = set()
            for j, neg in enumerate(raw_neg):
                nwhere = f"{where}.not_personal_data[{j}]"
                if not isinstance(neg, dict):
                    raise ConfigError(f"{name}: {nwhere} must be a mapping")
                extra = set(neg) - _NEGATIVE_KEYS
                if extra:
                    raise ConfigError(f"{name}: {nwhere} has unknown keys: {sorted(extra)}")
                missing = _NEGATIVE_KEYS - set(neg)
                if missing:
                    raise ConfigError(f"{name}: {nwhere} is missing keys: {sorted(missing)}")
                if not _name_field(neg["table"]):
                    raise ConfigError(f"{name}: {nwhere}.table must be a non-empty string")
                if not _name_field(neg["column"]):
                    raise ConfigError(f"{name}: {nwhere}.column must be a non-empty string")
                entry_neg = (neg["table"], neg["column"])
                if entry_neg in neg_set:
                    raise ConfigError(f"{name}: duplicate label at {nwhere}")
                if entry_neg in positive_columns:
                    raise ConfigError(
                        f"{name}: {nwhere} labels a column as both personal and not personal data"
                    )
                neg_set.add(entry_neg)
            negatives = tuple(sorted(neg_set))

        sources.append(
            LabeledSource(
                source_key=key,
                positives=tuple(sorted(positives)),
                not_personal_data=negatives,
            )
        )

    return LabelSet(label_origin=origin, sources=tuple(sources))


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


@dataclass(frozen=True)
class ClassMetrics:
    class_id: str
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float | None  # None when TP + FP == 0 (no predictions for this class)
    recall: float | None  # None when TP + FN == 0 (no labelled positives for this class)

    def to_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.class_id,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "precision": self.precision,
            "recall": self.recall,
        }


@dataclass(frozen=True)
class EvaluationReport:
    taxonomy_status: str
    rules_status: str
    label_origin: str
    scope_notice: str
    labelled_source_count: int
    labelled_column_count: int
    unscored_finding_count: int  # findings in sources that have no labels (not scored)
    ambiguous_columns: tuple[AmbiguousColumn, ...]
    per_class: tuple[ClassMetrics, ...]  # registry order

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_notice": self.scope_notice,
            "label_origin": self.label_origin,
            "taxonomy_status": self.taxonomy_status,
            "rules_status": self.rules_status,
            "labelled_source_count": self.labelled_source_count,
            "labelled_column_count": self.labelled_column_count,
            "unscored_finding_count": self.unscored_finding_count,
            "ambiguous_columns": [
                {
                    "source_key": a.source_key,
                    "table": a.table_name,
                    "column": a.column_name,
                    "class_ids": list(a.class_ids),
                }
                for a in self.ambiguous_columns
            ],
            "classes": [m.to_dict() for m in self.per_class],
        }


def evaluate(
    findings: Sequence[Finding],
    labels: LabelSet,
    profiles_by_key: Mapping[str, SourceProfile],
    *,
    registry: ClassRegistry,
    ruleset: RuleSet,
) -> EvaluationReport:
    """Score `findings` against `labels`. See the module docstring for the definitions.

    Every labelled source must be present in `profiles_by_key`, and every labelled column
    must exist in its profile (ConfigError otherwise: a typo must not silently become a
    false negative). Findings from sources without labels are not scored and are counted in
    `unscored_finding_count`. Duplicate finding identities or unknown classes raise ValueError.
    """
    class_ids = registry.ids()
    class_set = set(class_ids)

    columns_by_source: dict[str, set[tuple[str, str]]] = {}
    labelled_column_count = 0
    truth: dict[str, set[tuple[str, str, str, str]]] = {c: set() for c in class_ids}
    for i, source in enumerate(labels.sources):
        profile = profiles_by_key.get(source.source_key)
        if profile is None:
            raise ConfigError(f"labels sources[{i}] is not among the profiled sources")
        cols = {(t.name, c.name) for t in profile.tables for c in t.columns}
        columns_by_source[source.source_key] = cols
        labelled_column_count += len(cols)
        for j, (table, column, class_id) in enumerate(source.positives):
            if (table, column) not in cols:
                raise ConfigError(
                    f"labels sources[{i}].positives[{j}] refers to a column that is not "
                    "in the profiled source"
                )
            if class_id not in class_set:
                raise ConfigError(f"labels sources[{i}].positives[{j}] has an unknown class")
            truth[class_id].add((source.source_key, table, column, class_id))
        if source.not_personal_data is not None:
            for j, (table, column) in enumerate(source.not_personal_data):
                if (table, column) not in cols:
                    raise ConfigError(
                        f"labels sources[{i}].not_personal_data[{j}] refers to a column that "
                        "is not in the profiled source"
                    )
            labelled = {(t, c) for t, c, _ in source.positives} | set(source.not_personal_data)
            unlabelled = len(cols - labelled)
            if unlabelled:
                raise ConfigError(
                    f"labels sources[{i}] leaves {unlabelled} profiled column(s) unlabelled"
                )

    predicted: dict[str, set[tuple[str, str, str, str]]] = {c: set() for c in class_ids}
    seen: set[tuple[str, str, str, str]] = set()
    unscored = 0
    scored_findings: list[Finding] = []
    for f in findings:
        if not isinstance(f, Finding):
            raise TypeError("findings must be Finding instances")
        if f.identity in seen:
            raise ValueError("duplicate finding identity")
        seen.add(f.identity)
        if f.class_id not in class_set:
            raise ValueError("finding refers to a class that is not in the registry")
        cols = columns_by_source.get(f.source_key)
        if cols is None:
            unscored += 1
            continue
        if (f.table_name, f.column_name) not in cols:
            raise ValueError("finding refers to a column that is not in its profiled source")
        predicted[f.class_id].add(f.identity)
        scored_findings.append(f)

    per_class: list[ClassMetrics] = []
    for class_id in class_ids:
        tp = len(predicted[class_id] & truth[class_id])
        fp = len(predicted[class_id] - truth[class_id])
        fn = len(truth[class_id] - predicted[class_id])
        per_class.append(
            ClassMetrics(
                class_id=class_id,
                true_positives=tp,
                false_positives=fp,
                false_negatives=fn,
                precision=_ratio(tp, tp + fp),
                recall=_ratio(tp, tp + fn),
            )
        )

    logger.debug(
        "evaluation: classes=%d labelled_sources=%d scored_findings=%d unscored_findings=%d",
        len(class_ids), len(labels.sources), len(scored_findings), unscored,
    )
    return EvaluationReport(
        taxonomy_status=registry.taxonomy_status,
        rules_status=ruleset.rules_status,
        label_origin=labels.label_origin,
        scope_notice=SCOPE_NOTICES[labels.label_origin],
        labelled_source_count=len(labels.sources),
        labelled_column_count=labelled_column_count,
        unscored_finding_count=unscored,
        ambiguous_columns=ambiguous_columns(scored_findings),
        per_class=tuple(per_class),
    )
