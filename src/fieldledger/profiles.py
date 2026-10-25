"""Typed, stable profile model. Structural and aggregate metadata only.

Nothing here stores or returns cell values. `inferred_type` is a *broad storage/format
type*; it is NOT a personal-data class and says nothing about whether a column is
personal data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

PROFILE_SCHEMA_VERSION = 1


class SourceType(str, Enum):
    CSV = "csv"
    SQLITE = "sqlite"


class TypeBasis(str, Enum):
    """How `inferred_type` was obtained for a whole source."""

    OBSERVED_VALUES = "observed_values"  # CSV: every non-empty cell was tested
    DECLARED_SCHEMA = "declared_schema"  # SQLite: declared column type only; no values read


class BroadType(str, Enum):
    INTEGER = "integer"
    FLOAT = "float"
    NUMERIC = "numeric"  # SQLite NUMERIC affinity: may hold integers, reals or text
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    STRING = "string"
    BINARY = "binary"
    EMPTY = "empty"  # CSV column with no non-empty cell
    UNKNOWN = "unknown"  # SQLite column with no declared type


def _check_counts(non_null: int | None, null: int | None) -> None:
    if (non_null is None) != (null is None):
        raise ValueError("non_null_count and null_count must both be set or both be None")
    for v in (non_null, null):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
            raise ValueError("counts must be non-negative integers")


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    position: int  # 1-based
    inferred_type: BroadType
    non_null_count: int | None = None
    null_count: int | None = None
    declared_type: str | None = None  # SQLite schema text; None for CSV

    def __post_init__(self) -> None:
        _check_counts(self.non_null_count, self.null_count)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "position": self.position,
            "inferred_type": self.inferred_type.value,
            "declared_type": self.declared_type,
            "non_null_count": self.non_null_count,
            "null_count": self.null_count,
        }


@dataclass(frozen=True)
class TableProfile:
    """One CSV file (identified by its stem) or one SQLite table."""

    name: str
    columns: tuple[ColumnProfile, ...]
    row_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "columns", tuple(self.columns))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "row_count": self.row_count,
            "columns": [c.to_dict() for c in self.columns],
        }


@dataclass(frozen=True)
class SourceProfile:
    source_type: SourceType
    source_name: str  # file name only, never an absolute path
    type_basis: TypeBasis
    tables: tuple[TableProfile, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "tables", tuple(self.tables))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "source_type": self.source_type.value,
            "source_name": self.source_name,
            "type_basis": self.type_basis.value,
            "tables": [t.to_dict() for t in self.tables],
        }
