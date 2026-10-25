"""Broad type inference. Deterministic, strict, and value-free in its output.

CSV: `ColumnTypeAccumulator` tests every non-empty cell against strict ASCII patterns and
keeps only the running candidate set; it never stores a cell.

SQLite: `broad_type_from_declared` maps the *declared* column type text using SQLite's
documented affinity rules plus a few name hints. No values are read for typing.

Limitations (also in docs/ingestion.md):
- Types describe format, not meaning. They are not personal-data classes.
- Only these formats are recognised: plain decimal integers/floats (no thousands
  separators, no leading zeros such as 007, no NaN/Infinity), booleans spelled
  true/false/yes/no (any case; 0/1 count as integers), ISO-8601 dates (YYYY-MM-DD) and
  datetimes (YYYY-MM-DD[T or space]HH:MM[:SS[.ffffff]][Z|+HH:MM]).
- A column is typed only if ALL non-empty cells fit; otherwise it is `string`.
- SQLite's dynamic typing means stored values can differ from the declared type.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Callable

from .profiles import BroadType

_INT = re.compile(r"[+-]?(?:0|[1-9][0-9]{0,63})", re.ASCII)
_FLOAT = re.compile(
    r"[+-]?(?:(?:0|[1-9][0-9]{0,63})(?:\.[0-9]{1,64})?|\.[0-9]{1,64})(?:[eE][+-]?[0-9]{1,4})?",
    re.ASCII,
)
_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", re.ASCII)
_DATETIME = re.compile(
    r"(?P<y>[0-9]{4})-(?P<mo>[0-9]{2})-(?P<d>[0-9]{2})[T ]"
    r"(?P<h>[0-9]{2}):(?P<mi>[0-9]{2})(?::(?P<s>[0-9]{2})(?:\.[0-9]{1,6})?)?"
    r"(?:Z|[+-](?P<th>[0-9]{2}):(?P<tm>[0-9]{2}))?",
    re.ASCII,
)
_BOOLS = frozenset({"true", "false", "yes", "no"})


def _is_int(v: str) -> bool:
    return _INT.fullmatch(v) is not None


def _is_float(v: str) -> bool:
    return _FLOAT.fullmatch(v) is not None


def _is_bool(v: str) -> bool:
    return v.casefold() in _BOOLS


def _is_date(v: str) -> bool:
    m = _DATE.fullmatch(v)
    if not m:
        return False
    try:
        datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return False
    return True


def _is_datetime(v: str) -> bool:
    m = _DATETIME.fullmatch(v)
    if not m:
        return False
    try:
        datetime(
            int(m["y"]), int(m["mo"]), int(m["d"]),
            int(m["h"]), int(m["mi"]), int(m["s"] or 0),
        )
    except ValueError:
        return False
    if m["th"] is not None and (int(m["th"]) > 23 or int(m["tm"]) > 59):
        return False
    return True


_CHECKS: dict[BroadType, Callable[[str], bool]] = {
    BroadType.INTEGER: _is_int,
    BroadType.FLOAT: _is_float,
    BroadType.BOOLEAN: _is_bool,
    BroadType.DATETIME: _is_datetime,
    BroadType.DATE: _is_date,
}
# Resolution order when several candidates survive (int is also a valid float).
_ORDER = (
    BroadType.INTEGER,
    BroadType.FLOAT,
    BroadType.BOOLEAN,
    BroadType.DATETIME,
    BroadType.DATE,
)


class ColumnTypeAccumulator:
    """Streaming per-column accumulator. Holds counts and a candidate set only.

    A cell is null when it is empty or whitespace-only. CSV has no real NULL, so literal
    text such as "NULL" or "N/A" is NOT treated as null.
    """

    __slots__ = ("non_null", "null", "_candidates")

    def __init__(self) -> None:
        self.non_null = 0
        self.null = 0
        self._candidates: set[BroadType] = set(_ORDER)

    def observe(self, cell: str) -> None:
        value = cell.strip()
        if not value:
            self.null += 1
            return
        self.non_null += 1
        for t in tuple(self._candidates):
            if not _CHECKS[t](value):
                self._candidates.discard(t)

    def result(self) -> BroadType:
        if self.non_null == 0:
            return BroadType.EMPTY
        for t in _ORDER:
            if t in self._candidates:
                return t
        return BroadType.STRING


def broad_type_from_declared(declared: str | None) -> BroadType:
    """Map a SQLite declared type to a broad type (SQLite section 3.1 affinity rules).

    Quirk kept on purpose: any name containing "INT" is INTEGER affinity (e.g. "POINT").
    A column with no declared type is UNKNOWN. Anything not matched by the text/blob/real/
    integer rules has NUMERIC affinity (e.g. NUMERIC, DECIMAL, TIME).
    """
    t = (declared or "").strip().upper()
    if not t:
        return BroadType.UNKNOWN
    if t.startswith("BOOL"):
        return BroadType.BOOLEAN
    if t.startswith(("DATETIME", "TIMESTAMP")):
        return BroadType.DATETIME
    if t.startswith("DATE"):
        return BroadType.DATE
    if "INT" in t:
        return BroadType.INTEGER
    if any(s in t for s in ("CHAR", "CLOB", "TEXT")):
        return BroadType.STRING
    if "BLOB" in t:
        return BroadType.BINARY
    if any(s in t for s in ("REAL", "FLOA", "DOUB")):
        return BroadType.FLOAT
    return BroadType.NUMERIC
