"""Strictly read-only SQLite profiling. Structure and counts only; no row values.

Defence in depth (each layer is tested):
1. Path must resolve inside the allowed base and carry a SQLite file extension.
2. The 16-byte SQLite magic header is checked before SQLite is involved.
3. The connection URI is built from ``Path.as_uri()`` (percent-encodes ``? # % space`` and
   non-ASCII, and yields ``file:///C:/...`` on Windows) plus a fixed ``?mode=ro``.
   UNC / network paths (URI authority) are refused instead of being guessed at.
4. After opening: ``PRAGMA query_only=ON``, ``trusted_schema=OFF`` (schema-defined SQL
   functions are not trusted), ``cell_size_check=ON``; extension loading disabled.
5. The only SQL ever run is fixed text in this module plus identifiers that passed
   `quote_identifier`. Metadata comes from ``sqlite_master`` and ``pragma_table_xinfo(?)``
   (table name bound as a parameter). No caller-supplied SQL exists in the API.
6. A progress handler enforces the wall-clock budget; result processing is bounded by
   `max_tables` and `max_columns`.

Scope: ordinary tables only. Internal ``sqlite_*`` tables, views and virtual tables are
excluded. Row/null counts need one full scan per table (COUNT aggregates). Column types
are the DECLARED types; no values are read to infer them.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path, PurePath
from urllib.parse import urlsplit

from ._files import regular_file_size
from .errors import (
    DatabaseTimeoutError,
    InputAccessError,
    InputTooLargeError,
    InvalidDatabaseError,
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
from .typeinfer import broad_type_from_declared

logger = logging.getLogger(__name__)

SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".db3")
_MAGIC = b"SQLite format 3\x00"
_MAX_IDENTIFIER_CHARS = 256
_MAX_LOCK_WAIT_SECONDS = 5.0
_PROGRESS_OPS = 1000  # VM instructions between time-budget checks


def quote_identifier(name: object) -> str:
    """Return `name` as a double-quoted SQL identifier, or raise InvalidDatabaseError.

    Rejects non-strings, empty names, names over 256 characters, and any control character
    (including NUL and newlines, which would also corrupt logs). Embedded double quotes are
    doubled, which is SQLite's standard escape.
    """
    if (
        not isinstance(name, str)
        or not name
        or len(name) > _MAX_IDENTIFIER_CHARS
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        raise InvalidDatabaseError("database contains an unusable table or column name")
    return '"' + name.replace('"', '""') + '"'


def _build_readonly_uri(resolved: PurePath) -> str:
    """Build ``file:`` URI with ``mode=ro`` from an absolute, already-resolved path."""
    base = resolved.as_uri()
    if urlsplit(base).netloc:
        raise InputAccessError("network (UNC) database paths are not supported")
    if "?" in base or "#" in base:  # as_uri() encodes both; defensive only
        raise InputAccessError("database path cannot be expressed safely as a URI")
    return base + "?mode=ro"


def _open_readonly(target: Path, limits: IngestionLimits) -> sqlite3.Connection:
    """Open `target` read-only and harden the connection. Caller must close it."""
    uri = _build_readonly_uri(target)
    conn = sqlite3.connect(
        uri,
        uri=True,
        timeout=min(limits.sqlite_timeout_seconds, _MAX_LOCK_WAIT_SECONDS),
    )
    try:
        enable = getattr(conn, "enable_load_extension", None)
        if enable is not None:
            enable(False)
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA trusted_schema = OFF")
        conn.execute("PRAGMA cell_size_check = ON")
    except BaseException:
        conn.close()
        raise
    return conn


def _check_magic(target: Path) -> None:
    try:
        with open(target, "rb") as fh:
            header = fh.read(len(_MAGIC))
    except OSError:
        raise InputAccessError("input file cannot be opened") from None
    if header != _MAGIC:
        raise InvalidDatabaseError("file is not a SQLite database")


def _list_tables(conn: sqlite3.Connection, limits: IngestionLimits) -> list[str]:
    names: list[str] = []
    cursor = conn.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name")
    for name, sql in cursor:
        if not isinstance(name, str):
            raise InvalidDatabaseError("database contains an unusable table or column name")
        if name.lower().startswith("sqlite_"):  # reserved internal tables
            continue
        if isinstance(sql, str) and sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE"):
            continue
        names.append(name)
        if len(names) > limits.max_tables:
            raise InputTooLargeError("table limit exceeded")
    return names


def _profile_table(conn: sqlite3.Connection, table: str, limits: IngestionLimits) -> TableProfile:
    quoted_table = quote_identifier(table)
    info = conn.execute(
        "SELECT name, type, hidden FROM pragma_table_xinfo(?)", (table,)
    ).fetchmany(limits.max_columns + 1)
    if len(info) > limits.max_columns:
        raise InputTooLargeError("column limit exceeded")
    # hidden: 0 normal, 2/3 generated columns (kept); 1 = hidden virtual-table column (dropped)
    cols = [(name, declared) for name, declared, hidden in info if hidden != 1]
    if not cols:
        return TableProfile(name=table, columns=(), row_count=0)

    select_list = ", ".join(["COUNT(*)"] + [f"COUNT({quote_identifier(n)})" for n, _ in cols])
    # Identifiers above are validated and quoted; no caller-supplied text reaches this SQL.
    counts = conn.execute(f"SELECT {select_list} FROM {quoted_table}").fetchone()
    row_count = counts[0]
    columns = tuple(
        ColumnProfile(
            name=name,
            position=i,
            inferred_type=broad_type_from_declared(declared),
            non_null_count=counts[i],
            null_count=row_count - counts[i],
            declared_type=declared or None,
        )
        for i, (name, declared) in enumerate(cols, start=1)
    )
    return TableProfile(name=table, columns=columns, row_count=row_count)


def _map_operational(exc: sqlite3.OperationalError) -> Exception:
    msg = str(exc).lower()
    if "interrupt" in msg or "locked" in msg or "busy" in msg:
        return DatabaseTimeoutError("database profiling timed out or the database is locked")
    if "decode" in msg:
        return InvalidDatabaseError("database contains text that is not valid UTF-8")
    return InputAccessError("database could not be opened or read")


def profile_sqlite(
    path: str | Path,
    allowed_base: str | Path,
    limits: IngestionLimits | None = None,
) -> SourceProfile:
    """Profile every ordinary user table of one SQLite file inside `allowed_base`.

    A 0-byte file is a valid empty database and yields a profile with no tables.
    """
    limits = limits or DEFAULT_LIMITS
    target = resolve_within(allowed_base, path)
    if target.suffix.lower() not in SQLITE_SUFFIXES:
        raise UnsupportedFileTypeError("unsupported database file extension")
    size = regular_file_size(target)
    if size > limits.max_db_bytes:
        raise InputTooLargeError("file exceeds the size limit")

    tables: list[TableProfile] = []
    if size > 0:
        _check_magic(target)
        try:
            with closing(_open_readonly(target, limits)) as conn:
                deadline = time.monotonic() + limits.sqlite_timeout_seconds
                conn.set_progress_handler(
                    lambda: 1 if time.monotonic() > deadline else 0, _PROGRESS_OPS
                )
                for table in _list_tables(conn, limits):
                    tables.append(_profile_table(conn, table, limits))
        except sqlite3.OperationalError as exc:
            raise _map_operational(exc) from None
        except sqlite3.DatabaseError:
            raise InvalidDatabaseError("file is not a valid or readable SQLite database") from None
        except sqlite3.Error:
            raise InputAccessError("database could not be opened or read") from None

    logger.debug("sqlite profiled: tables=%d", len(tables))
    return SourceProfile(
        source_type=SourceType.SQLITE,
        source_name=target.name,
        type_basis=TypeBasis.DECLARED_SCHEMA,
        tables=tuple(tables),
    )
