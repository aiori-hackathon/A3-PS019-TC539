"""Finding model and source identity for Milestone 4.

A `Finding` is a TECHNICAL CANDIDATE: "a rule matched this column's name". It carries no
legal, compliance, lawful-basis or risk conclusion and no cell values. Human review is
required before anything is concluded from it.

Identity: detection and evaluation both key on exactly
``(source_key, table_name, column_name, class_id)`` (`Finding.identity`). ``source_key`` is
the POSIX path of the file actually read, relative to the approved input base, so two files
with the same name in different directories stay distinguishable (``SourceProfile.source_name``
is a bare file name and is not enough). Build it with `source_key_for`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .safety import resolve_within

EVIDENCE_KIND = "column_name_match"
EVIDENCE_NOTICE = (
    "Technical candidate based on column-name rule matching only. It is not a legal "
    "determination, lawful-basis assessment or compliance decision. Human review is required."
)
MAX_SOURCE_KEY_CHARS = 1024

FindingIdentity = tuple[str, str, str, str]


def validate_source_key(value: object) -> str:
    """Return `value` if it is a safe relative POSIX path key, else raise ValueError."""
    if not isinstance(value, str) or not value or len(value) > MAX_SOURCE_KEY_CHARS:
        raise ValueError("invalid source_key")
    if value.startswith("/") or "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid source_key")
    if len(value) >= 2 and value[1] == ":" and value[0].isalpha():  # drive-letter path
        raise ValueError("invalid source_key")
    if any(seg in ("", ".", "..") for seg in value.split("/")):
        raise ValueError("invalid source_key")
    return value


def source_key_for(path: str | Path, allowed_base: str | Path) -> str:
    """Stable identity of the file `path` inside `allowed_base`.

    The path is resolved exactly as the profilers resolve it (symlinks followed), so the key
    names the file that is actually read. Raises PathAccessError if it escapes the base.
    """
    base = Path(allowed_base).resolve()
    target = resolve_within(base, path)
    return validate_source_key(target.relative_to(base).as_posix())


@dataclass(frozen=True)
class Finding:
    source_key: str
    table_name: str
    column_name: str
    column_position: int  # 1-based, as in ColumnProfile.position
    class_id: str
    rule_ids: tuple[str, ...]
    evidence_kind: str = EVIDENCE_KIND

    def __post_init__(self) -> None:
        validate_source_key(self.source_key)
        for label in ("table_name", "column_name", "class_id"):
            value = getattr(self, label)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{label} must be a non-empty string")
        pos = self.column_position
        if isinstance(pos, bool) or not isinstance(pos, int) or pos < 1:
            raise ValueError("column_position must be a positive integer")
        if not isinstance(self.rule_ids, (tuple, list)):
            raise ValueError("rule_ids must be a tuple of rule ids")
        rule_ids = tuple(sorted(set(self.rule_ids)))
        if not rule_ids or not all(isinstance(r, str) and r for r in rule_ids):
            raise ValueError("rule_ids must contain at least one non-empty rule id")
        object.__setattr__(self, "rule_ids", rule_ids)
        if self.evidence_kind != EVIDENCE_KIND:
            raise ValueError("unsupported evidence_kind")

    @property
    def column_identity(self) -> tuple[str, str, str]:
        return (self.source_key, self.table_name, self.column_name)

    @property
    def identity(self) -> FindingIdentity:
        return (self.source_key, self.table_name, self.column_name, self.class_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "table": self.table_name,
            "column": self.column_name,
            "position": self.column_position,
            "class_id": self.class_id,
            "rule_ids": list(self.rule_ids),
            "evidence_kind": self.evidence_kind,
        }
