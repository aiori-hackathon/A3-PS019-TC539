"""Configurable column-name rules (Milestone 4). Holds NO rules: they come from YAML only.

Rules match COLUMN NAMES only. No cell value is ever read or needed. A rule says "a column
whose name contains this token sequence (or equals it) is a candidate for class X". That is
technical evidence for human review, never a legal determination.

File format (see config/rules.yaml):

    rules_status: provisional | confirmed
    rules:
      - id: RULE_ID                # unique, case-insensitive
        class_id: <id from the class registry, exact match>
        match: token | exact
        patterns: [name or phrase, ...]
        exclude_patterns: [phrase, ...]   # optional, see below

Matching (both sides are normalised with `tokenize_name`):
- ``token``: the pattern's tokens occur as a contiguous run inside the column's tokens.
  Whole tokens only: pattern ``abc`` matches ``abc_x`` and ``xAbc`` but not ``abcd``.
- ``exact``: the column's tokens equal the pattern's tokens.
There is no regular-expression or substring matching (avoids ReDoS and accidental hits).

Exclusions (optional, per rule): if any ``exclude_patterns`` phrase occurs as a contiguous token
run in the column name, THIS rule does not fire for that column. Other rules are unaffected, so
an exclusion never hides a match made by a different rule. Exclusions exist to remove keyword
look-alikes (for example ``phone_model`` for a phone rule) and are deliberately narrow:
every exclusion must have at least ``MIN_EXCLUDE_TOKENS`` tokens, so a single broad word such
as ``count`` can never be excluded, and an exclusion may not equal one of the rule's own
patterns. The detector can report which exclusion suppressed which rule (`explain_column`).

The file is read with the strict loader in strict_yaml.py (safe loader; duplicate keys,
aliases and merge keys rejected). Error messages give the file name, an index and a fixed
description. They never echo patterns, rule text or column/table names.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .classes import ClassRegistry
from .errors import ConfigError
from .strict_yaml import load_strict_yaml_mapping

MAX_RULES = 200
MAX_PATTERNS_PER_RULE = 100
MAX_PATTERN_CHARS = 64
MAX_PATTERN_TOKENS = 8
MIN_EXCLUDE_TOKENS = 2  # exclusions must be phrases; a single word is too broad
MATCH_KINDS = ("token", "exact")
RULES_STATUSES = ("provisional", "confirmed")

_TOP_KEYS = {"rules_status", "rules"}
_RULE_KEYS = {"id", "class_id", "match", "patterns"}  # required
_OPTIONAL_RULE_KEYS = {"exclude_patterns"}
_RULE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,63}$")
_ACRONYM_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")
_SPLIT_RE = re.compile(r"[\W_]+")

Pattern = tuple[str, ...]


def tokenize_name(name: str) -> tuple[str, ...]:
    """Normalise a column name or pattern into lower-case tokens.

    NFKC-normalise, split camelCase and acronym boundaries, split on every non-word
    character and underscore, then casefold. Letters and digits are NOT split from each
    other, so ``tc1`` stays one token and ``tc10`` is a different one.
    """
    text = unicodedata.normalize("NFKC", name)
    text = _ACRONYM_RE.sub(r"\1 \2", text)
    text = _CAMEL_RE.sub(r"\1 \2", text)
    return tuple(t.casefold() for t in _SPLIT_RE.split(text) if t)


@dataclass(frozen=True)
class Rule:
    id: str
    class_id: str
    match: str
    patterns: tuple[Pattern, ...]  # normalised, de-duplicated, sorted
    exclude_patterns: tuple[Pattern, ...] = ()  # normalised, de-duplicated, sorted; may be empty


@dataclass(frozen=True)
class RuleSet:
    rules_status: str
    rules: tuple[Rule, ...]

    @property
    def is_provisional(self) -> bool:
        return self.rules_status != "confirmed"

    def rule_ids(self) -> tuple[str, ...]:
        return tuple(r.id for r in self.rules)


def _parse_patterns(
    raw: object, where: str, name: str, *, field: str = "patterns", min_tokens: int = 1
) -> tuple[Pattern, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{name}: {where}.{field} must be a non-empty list")
    if len(raw) > MAX_PATTERNS_PER_RULE:
        raise ConfigError(f"{name}: {where}.{field} has too many entries")
    normalised: set[Pattern] = set()
    for j, item in enumerate(raw):
        if not isinstance(item, str):
            raise ConfigError(
                f"{name}: {where}.{field}[{j}] must be a string "
                "(quote values such as yes, no, on, off, dates and numbers)"
            )
        if len(item) > MAX_PATTERN_CHARS:
            raise ConfigError(f"{name}: {where}.{field}[{j}] is too long")
        tokens = tokenize_name(item)
        if not tokens:
            raise ConfigError(f"{name}: {where}.{field}[{j}] contains no usable tokens")
        if len(tokens) > MAX_PATTERN_TOKENS:
            raise ConfigError(f"{name}: {where}.{field}[{j}] has too many tokens")
        if len(tokens) < min_tokens:
            raise ConfigError(
                f"{name}: {where}.{field}[{j}] must have at least {min_tokens} tokens "
                "(single-word exclusions are too broad)"
            )
        normalised.add(tokens)
    return tuple(sorted(normalised))


def load_rule_set(path: str | Path, registry: ClassRegistry) -> RuleSet:
    """Load and validate a rules file against `registry`. Raises ConfigError on any problem."""
    name = Path(path).name
    data = load_strict_yaml_mapping(path)

    unknown = set(data) - _TOP_KEYS
    if unknown:
        raise ConfigError(f"{name}: unknown top-level keys: {sorted(unknown)}")

    status = data.get("rules_status")
    if status not in RULES_STATUSES:
        raise ConfigError(f"{name}: rules_status must be one of {list(RULES_STATUSES)}")
    if status == "confirmed" and registry.is_provisional:
        raise ConfigError(
            f"{name}: rules_status 'confirmed' cannot be used with a provisional class taxonomy"
        )

    raw = data.get("rules")
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{name}: rules must be a non-empty list")
    if len(raw) > MAX_RULES:
        raise ConfigError(f"{name}: too many rules")

    valid_class_ids = registry.ids()
    seen_ids: set[str] = set()
    rules: list[Rule] = []
    for i, item in enumerate(raw):
        where = f"rules[{i}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{name}: {where} must be a mapping")
        extra = set(item) - _RULE_KEYS - _OPTIONAL_RULE_KEYS
        if extra:
            raise ConfigError(f"{name}: {where} has unknown keys: {sorted(extra)}")
        missing = _RULE_KEYS - set(item)
        if missing:
            raise ConfigError(f"{name}: {where} is missing keys: {sorted(missing)}")

        rule_id = item["id"]
        if not isinstance(rule_id, str) or not _RULE_ID_RE.match(rule_id):
            raise ConfigError(f"{name}: {where}.id is missing or invalid")
        if rule_id.casefold() in seen_ids:
            raise ConfigError(f"{name}: duplicate rule id at {where}")
        seen_ids.add(rule_id.casefold())

        class_id = item["class_id"]
        if not isinstance(class_id, str) or class_id not in valid_class_ids:
            raise ConfigError(f"{name}: {where}.class_id is not in the class registry")

        match = item["match"]
        if match not in MATCH_KINDS:
            raise ConfigError(f"{name}: {where}.match must be one of {list(MATCH_KINDS)}")

        patterns = _parse_patterns(item["patterns"], where, name)
        excludes: tuple[Pattern, ...] = ()
        if "exclude_patterns" in item:
            excludes = _parse_patterns(
                item["exclude_patterns"],
                where,
                name,
                field="exclude_patterns",
                min_tokens=MIN_EXCLUDE_TOKENS,
            )
            if set(excludes) & set(patterns):
                raise ConfigError(
                    f"{name}: {where}.exclude_patterns must not repeat one of the rule's patterns"
                )
        rules.append(
            Rule(
                id=rule_id,
                class_id=class_id,
                match=match,
                patterns=patterns,
                exclude_patterns=excludes,
            )
        )

    return RuleSet(rules_status=status, rules=tuple(rules))
