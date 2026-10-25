"""Safe, bounded CSV profiling. Returns structure and counts only; never cell values.

Behaviour (each rule is covered by tests):
- Path must resolve inside the allowed base (`resolve_within`) and end in ``.csv``.
- Encoding: UTF-8 with or without BOM (``utf-8-sig``). No silent guessing. A different
  encoding must be requested explicitly via ``encoding=`` from a short allow-list.
- Delimiter is fixed to a comma; quoting is strict (stray/unterminated quotes are errors).
- The first non-blank row is the header. Blank lines are skipped and are not counted as rows.
- Empty file / no header row            -> MalformedCsvError
- Blank header cell (after strip)       -> MalformedCsvError
- Duplicate header (strip + casefold)   -> MalformedCsvError (never renamed or merged)
- Row with a different field count      -> MalformedCsvError at the first such line
- NUL byte, undecodable bytes, over-long line, csv syntax error -> MalformedCsvError
- A header-only file is valid: 0 rows, every column typed ``empty``.
- Limits (see `IngestionLimits`): file bytes, data rows, columns, line length.
Error messages carry line numbers and counts only: never values, header text or paths.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Iterator

from ._files import regular_file_size
from .errors import (
    ConfigError,
    InputAccessError,
    InputTooLargeError,
    MalformedCsvError,
    UnsupportedFileTypeError,
)
from .limits import DEFAULT_LIMITS, IngestionLimits
from .profiles import (
    ColumnProfile,
    SourceProfile,
    SourceType,
    TableProfile,
    TypeBasis,
)
from .safety import resolve_within
from .typeinfer import ColumnTypeAccumulator

logger = logging.getLogger(__name__)

DEFAULT_ENCODING = "utf-8-sig"
ALLOWED_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


class _LimitedRaw(io.RawIOBase):
    """Raw reader that refuses to deliver more than `limit` bytes (guards against growth)."""

    def __init__(self, raw, limit: int) -> None:
        super().__init__()
        self._raw = raw
        self._limit = limit
        self._seen = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        data = self._raw.read(len(buffer))
        self._seen += len(data)
        if self._seen > self._limit:
            raise InputTooLargeError("file exceeds the size limit")
        buffer[: len(data)] = data
        return len(data)


def _checked_lines(text: io.TextIOBase, max_chars: int) -> Iterator[str]:
    """Yield physical lines, rejecting NUL bytes and over-long lines (bounded memory)."""
    while True:
        line = text.readline(max_chars + 1)
        if not line:
            return
        if len(line) > max_chars:
            raise MalformedCsvError("a line exceeds the maximum line length")
        if "\x00" in line:
            raise MalformedCsvError("file contains NUL characters")
        yield line


def _read_header(reader, limits: IngestionLimits) -> list[str]:
    for row in reader:
        if not row:  # blank line
            continue
        if len(row) > limits.max_columns:
            raise InputTooLargeError("column limit exceeded")
        names = [cell.strip() for cell in row]
        blanks = [i + 1 for i, n in enumerate(names) if not n]
        if blanks:
            raise MalformedCsvError(f"blank header name at column position(s) {blanks}")
        first_seen: dict[str, int] = {}
        for i, n in enumerate(names, start=1):
            key = n.casefold()
            if key in first_seen:
                raise MalformedCsvError(
                    f"duplicate header names at column positions {first_seen[key]} and {i}"
                )
            first_seen[key] = i
        return names
    raise MalformedCsvError("file is empty or has no header row")


def profile_csv(
    path: str | Path,
    allowed_base: str | Path,
    limits: IngestionLimits | None = None,
    *,
    encoding: str = DEFAULT_ENCODING,
) -> SourceProfile:
    """Profile one CSV file inside `allowed_base`. See module docstring for the rules."""
    limits = limits or DEFAULT_LIMITS
    if encoding not in ALLOWED_ENCODINGS:
        raise ConfigError("unsupported encoding option")

    target = resolve_within(allowed_base, path)
    if target.suffix.lower() != ".csv":
        raise UnsupportedFileTypeError("only .csv files are supported")
    size = regular_file_size(target)
    if size > limits.max_file_bytes:
        raise InputTooLargeError("file exceeds the size limit")

    try:
        raw = open(target, "rb")
    except OSError:
        raise InputAccessError("input file cannot be opened") from None

    with raw:
        text = io.TextIOWrapper(
            io.BufferedReader(_LimitedRaw(raw, limits.max_file_bytes)),
            encoding=encoding,
            newline="",
        )
        reader = csv.reader(
            _checked_lines(text, limits.max_line_chars),
            delimiter=",",
            quotechar='"',
            strict=True,
        )
        try:
            header = _read_header(reader, limits)
            width = len(header)
            accs = [ColumnTypeAccumulator() for _ in header]
            rows = 0
            for row in reader:
                if not row:  # blank line
                    continue
                if len(row) != width:
                    raise MalformedCsvError(
                        f"inconsistent row length at line {reader.line_num}: "
                        f"expected {width} fields, found {len(row)}"
                    )
                rows += 1
                if rows > limits.max_rows:
                    raise InputTooLargeError("row limit exceeded")
                for acc, cell in zip(accs, row):
                    acc.observe(cell)
        except UnicodeDecodeError:
            raise MalformedCsvError(f"file is not valid {encoding} text") from None
        except csv.Error:
            raise MalformedCsvError(f"malformed CSV near line {reader.line_num}") from None
        finally:
            text.close()

    columns = tuple(
        ColumnProfile(
            name=name,
            position=i,
            inferred_type=acc.result(),
            non_null_count=acc.non_null,
            null_count=acc.null,
        )
        for i, (name, acc) in enumerate(zip(header, accs), start=1)
    )
    logger.debug("csv profiled: columns=%d rows=%d", len(columns), rows)
    return SourceProfile(
        source_type=SourceType.CSV,
        source_name=target.name,
        type_basis=TypeBasis.OBSERVED_VALUES,
        tables=(TableProfile(name=target.stem, columns=columns, row_count=rows),),
    )
