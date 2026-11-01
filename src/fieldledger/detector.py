"""Rule-based candidate detection over structural profiles (Milestone 4).

Input is a `SourceProfile` (structure only) plus a `RuleSet`. Only column NAMES are examined;
no file is opened and no cell value is seen. Output is a deterministic tuple of `Finding`.

Ambiguity is explicit, never silently resolved: a column that matches rules of two classes
yields one finding per class, and `ambiguous_columns` lists those columns for review.

A rule may carry ``exclude_patterns`` (see rules.py). An excluded rule does not produce a
finding for that column, but the suppression is not lost: `explain_column` reports every rule
that matched the name, which pattern matched, and which exclusion (if any) suppressed it.
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


@dataclass(frozen=True)
class RuleMatch:
    """One rule's match on one column name, for explanation and tests.

    `matched_patterns` are the rule's include patterns found in the name (tokenised).
    `excluded_by` lists the rule's exclusion phrases found in the name; when it is non-empty
    the rule is suppressed and produces NO finding.
    """

    rule_id: str
    class_id: str
    matched_patterns: tuple[Pattern, ...]
    excluded_by: tuple[Pattern, ...] = ()

    @property
    def suppressed(self) -> bool:
        return bool(self.excluded_by)


def _freeze(index: dict[Pattern, list[_Hit]]) -> dict[Pattern, tuple[_Hit, ...]]:
    return {pattern: tuple(hits) for pattern, hits in index.items()}


@lru_cache(maxsize=16)
def _index(
    ruleset: RuleSet,
) -> tuple[
    dict[Pattern, tuple[_Hit, ...]],
    dict[Pattern, tuple[_Hit, ...]],
    int,
    dict[str, tuple[Pattern, ...]],
]:
    token_idx: dict[Pattern, list[_Hit]] = {}
    exact_idx: dict[Pattern, list[_Hit]] = {}
    excludes: dict[str, tuple[Pattern, ...]] = {}
    max_len = 1
    for rule in ruleset.rules:
        target = token_idx if rule.match == "token" else exact_idx
        for pattern in rule.patterns:
            target.setdefault(pattern, []).append((rule.id, rule.class_id))
            if rule.match == "token":
                max_len = max(max_len, len(pattern))
        if rule.exclude_patterns:
            excludes[rule.id] = rule.exclude_patterns
    return _freeze(token_idx), _freeze(exact_idx), max_len, excludes


def _contains_run(tokens: tuple[str, ...], phrase: Pattern) -> bool:
    n, m = len(tokens), len(phrase)
    return any(tokens[i : i + m] == phrase for i in range(n - m + 1))


def _match_tokens(tokens: tuple[str, ...], ruleset: RuleSet) -> list[RuleMatch]:
    """Every rule that matches `tokens`, suppressed or not, sorted by rule id."""
    token_idx, exact_idx, max_len, excludes = _index(ruleset)
    found: dict[str, tuple[str, set[Pattern]]] = {}
    for rule_id, class_id in exact_idx.get(tokens, ()):
        found.setdefault(rule_id, (class_id, set()))[1].add(tokens)
    n = len(tokens)
    for i in range(n):
        for length in range(1, min(max_len, n - i) + 1):
            window = tokens[i : i + length]
            for rule_id, class_id in token_idx.get(window, ()):
                found.setdefault(rule_id, (class_id, set()))[1].add(window)
    matches = []
    for rule_id in sorted(found):
        class_id, patterns = found[rule_id]
        excluded = tuple(sorted(p for p in excludes.get(rule_id, ()) if _contains_run(tokens, p)))
        matches.append(RuleMatch(rule_id, class_id, tuple(sorted(patterns)), excluded))
    return matches


def explain_column(column_name: str, ruleset: RuleSet) -> tuple[RuleMatch, ...]:
    """Explain how `ruleset` treats one column name: matched rules, patterns and suppressions.

    Uses exactly the matching code of `detect_candidates`, so the rule ids of the
    non-suppressed matches equal the `rule_ids` of the findings for that name.
    """
    return tuple(_match_tokens(tokenize_name(column_name), ruleset))


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
    findings: list[Finding] = []
    columns_seen = 0
    for table in profile.tables:
        for column in table.columns:
            columns_seen += 1
            tokens = tokenize_name(column.name)
            hits: dict[str, set[str]] = {}
            for match in _match_tokens(tokens, ruleset):
                if not match.suppressed:
                    hits.setdefault(match.class_id, set()).add(match.rule_id)
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
