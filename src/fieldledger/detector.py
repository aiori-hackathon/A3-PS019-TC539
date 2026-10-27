"""Rule-based candidate detection over structural profiles (Milestone 4).

Input is a `SourceProfile` (structure only) plus a `RuleSet`. Only column NAMES are examined;
no file is opened and no cell value is seen. Output is a deterministic tuple of `Finding`.

Ambiguity is explicit, never silently resolved: a column that matches rules of two classes
yields one finding per class, and `ambiguous_columns` lists those columns for review.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from .findings import Finding, validate_source_key
from .profiles import SourceProfile
from .rules import Pattern, RuleSet, tokenize_name

logger = logging.getLogger(__name__)

_Hit = tuple[str, str]  # (rule_id, class_id)


@dataclass(frozen=True)
class AmbiguousColumn:
    source_key: str
    table_name: str
    column_name: str
    class_ids: tuple[str, ...]  # sorted, at least two


def _freeze(index: dict[Pattern, list[_Hit]]) -> dict[Pattern, tuple[_Hit, ...]]:
    return {pattern: tuple(hits) for pattern, hits in index.items()}


@lru_cache(maxsize=16)
def _index(ruleset: RuleSet) -> tuple[dict[Pattern, tuple[_Hit, ...]], dict[Pattern, tuple[_Hit, ...]], int]:
    token_idx: dict[Pattern, list[_Hit]] = {}
    exact_idx: dict[Pattern, list[_Hit]] = {}
    max_len = 1
    for rule in ruleset.rules:
        target = token_idx if rule.match == "token" else exact_idx
        for pattern in rule.patterns:
            target.setdefault(pattern, []).append((rule.id, rule.class_id))
            if rule.match == "token":
                max_len = max(max_len, len(pattern))
    return _freeze(token_idx), _freeze(exact_idx), max_len


def detect_candidates(
    profile: SourceProfile,
    ruleset: RuleSet,
    *,
    source_key: str,
) -> tuple[Finding, ...]:
    """Return candidate findings for every table/column of `profile`, in a stable order.

    `source_key` identifies the source (see `findings.source_key_for`). Output is sorted by
    (table name, column position, class id), independent of rule order.
    """
    validate_source_key(source_key)
    token_idx, exact_idx, max_len = _index(ruleset)

    findings: list[Finding] = []
    columns_seen = 0
    for table in profile.tables:
        for column in table.columns:
            columns_seen += 1
            tokens = tokenize_name(column.name)
            hits: dict[str, set[str]] = {}
            for rule_id, class_id in exact_idx.get(tokens, ()):
                hits.setdefault(class_id, set()).add(rule_id)
            n = len(tokens)
            for i in range(n):
                for length in range(1, min(max_len, n - i) + 1):
                    for rule_id, class_id in token_idx.get(tokens[i : i + length], ()):
                        hits.setdefault(class_id, set()).add(rule_id)
            for class_id in sorted(hits):
                findings.append(
                    Finding(
                        source_key=source_key,
                        table_name=table.name,
                        column_name=column.name,
                        column_position=column.position,
                        class_id=class_id,
                        rule_ids=tuple(sorted(hits[class_id])),
                    )
                )

    findings.sort(key=lambda f: (f.table_name, f.column_position, f.class_id))
    logger.debug("detection: columns=%d findings=%d", columns_seen, len(findings))
    return tuple(findings)


def ambiguous_columns(findings: tuple[Finding, ...] | list[Finding]) -> tuple[AmbiguousColumn, ...]:
    """Columns with findings for two or more classes, sorted by identity."""
    grouped: dict[tuple[str, str, str], set[str]] = {}
    for f in findings:
        grouped.setdefault(f.column_identity, set()).add(f.class_id)
    return tuple(
        AmbiguousColumn(key[0], key[1], key[2], tuple(sorted(classes)))
        for key, classes in sorted(grouped.items())
        if len(classes) >= 2
    )
