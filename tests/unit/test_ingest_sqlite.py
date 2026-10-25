import hashlib
import json
import logging
import sqlite3
import sys
from contextlib import closing
from pathlib import PureWindowsPath

import pytest

from fieldledger.errors import (
    DatabaseTimeoutError,
    InputAccessError,
    InputTooLargeError,
    InvalidDatabaseError,
    PathAccessError,
    UnsupportedFileTypeError,
)
from fieldledger.ingest_sqlite import (
    _build_readonly_uri,
    _open_readonly,
    profile_sqlite,
    quote_identifier,
)
from fieldledger.limits import DEFAULT_LIMITS, IngestionLimits
from fieldledger.profiles import BroadType, SourceType, TypeBasis

SECRET = "zz-synthetic-sentinel-9035"


def make_db(path, *statements):
    with closing(sqlite3.connect(path)) as conn:
        for s in statements:
            if isinstance(s, tuple):
                conn.execute(*s)
            else:
                conn.execute(s)
        conn.commit()
    return path


def snapshot(directory):
    return {
        p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in sorted(directory.iterdir())
    }


@pytest.fixture
def users_db(tmp_path):
    return make_db(
        tmp_path / "app.db",
        "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email VARCHAR(50), "
        "score REAL, created DATETIME, active BOOLEAN, photo BLOB, misc)",
        (f"INSERT INTO users VALUES (1, '{SECRET}-n', '{SECRET}@example.test', 1.5, "
         "'2024-01-01 10:00', 1, x'00ff', NULL)",),
        "INSERT INTO users (id, name) VALUES (2, NULL)",
        "INSERT INTO users (id, name) VALUES (3, 'x')",
    )


def test_valid_database_profile(users_db, tmp_path):
    p = profile_sqlite("app.db", tmp_path)
    assert p.source_type is SourceType.SQLITE
    assert p.type_basis is TypeBasis.DECLARED_SCHEMA
    assert p.source_name == "app.db"
    (t,) = p.tables
    assert t.name == "users" and t.row_count == 3
    c = {col.name: col for col in t.columns}
    assert [col.name for col in t.columns] == [
        "id", "name", "email", "score", "created", "active", "photo", "misc",
    ]
    assert [col.position for col in t.columns] == list(range(1, 9))
    assert c["id"].inferred_type is BroadType.INTEGER
    assert c["name"].inferred_type is BroadType.STRING
    assert (c["name"].non_null_count, c["name"].null_count) == (2, 1)
    assert c["email"].declared_type == "VARCHAR(50)"
    assert (c["email"].non_null_count, c["email"].null_count) == (1, 2)
    assert c["score"].inferred_type is BroadType.FLOAT
    assert c["created"].inferred_type is BroadType.DATETIME
    assert c["active"].inferred_type is BroadType.BOOLEAN
    assert c["photo"].inferred_type is BroadType.BINARY
    assert c["misc"].inferred_type is BroadType.UNKNOWN and c["misc"].declared_type is None
    assert (c["misc"].non_null_count, c["misc"].null_count) == (0, 3)


def test_profile_contains_no_raw_values_or_paths(users_db, tmp_path):
    p = profile_sqlite("app.db", tmp_path)
    dumped = json.dumps(p.to_dict())
    assert SECRET not in dumped and SECRET not in repr(p)
    assert str(tmp_path) not in dumped


def test_logging_never_contains_values(users_db, tmp_path, caplog):
    with caplog.at_level(logging.DEBUG, logger="fieldledger"):
        profile_sqlite("app.db", tmp_path)
    assert SECRET not in caplog.text


def test_multiple_tables_sorted_by_name(tmp_path):
    make_db(
        tmp_path / "m.sqlite",
        "CREATE TABLE zeta (a INTEGER)",
        "CREATE TABLE alpha (x TEXT, y TEXT)",
        "CREATE TABLE mid (k NUMERIC)",
        "INSERT INTO alpha VALUES ('1', NULL)",
    )
    p = profile_sqlite("m.sqlite", tmp_path)
    assert [t.name for t in p.tables] == ["alpha", "mid", "zeta"]
    assert [len(t.columns) for t in p.tables] == [2, 1, 1]
    assert [t.row_count for t in p.tables] == [1, 0, 0]
    assert p.tables[1].columns[0].inferred_type is BroadType.NUMERIC
    assert profile_sqlite("m.sqlite", tmp_path) == p  # deterministic


def test_internal_tables_excluded(tmp_path):
    db = make_db(
        tmp_path / "i.db",
        "CREATE TABLE seq (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)",
        "INSERT INTO seq (v) VALUES ('a')",
        "CREATE INDEX idx_v ON seq (v)",
        "ANALYZE",
    )
    with closing(sqlite3.connect(db)) as conn:
        internal = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'sqlite_%'")}
    assert {"sqlite_sequence", "sqlite_stat1"} <= internal  # they really exist in the file
    p = profile_sqlite("i.db", tmp_path)
    assert [t.name for t in p.tables] == ["seq"]


def test_views_are_excluded(tmp_path):
    make_db(
        tmp_path / "v.db",
        "CREATE TABLE t (a INTEGER)",
        "CREATE VIEW v AS SELECT a FROM t",
    )
    assert [t.name for t in profile_sqlite("v.db", tmp_path).tables] == ["t"]


def test_virtual_tables_are_excluded(tmp_path):
    try:
        make_db(tmp_path / "f.db", "CREATE VIRTUAL TABLE docs USING fts5(body)")
    except sqlite3.OperationalError:
        pytest.skip("FTS5 not available in this SQLite build")
    names = [t.name for t in profile_sqlite("f.db", tmp_path).tables]
    assert "docs" not in names


def test_virtual_tables_with_leading_sql_comment_are_excluded(tmp_path):
    try:
        make_db(
            tmp_path / "commented.db",
            "/* leading comment */ CREATE VIRTUAL TABLE docs USING fts5(body)",
        )
    except sqlite3.OperationalError:
        pytest.skip("FTS5 not available in this SQLite build")

    names = [
        table.name
        for table in profile_sqlite("commented.db", tmp_path).tables
    ]
    assert "docs" not in names


def test_generated_columns_are_profiled(tmp_path):
    make_db(
        tmp_path / "g.db",
        "CREATE TABLE t (a INTEGER, b INTEGER GENERATED ALWAYS AS (a * 2) VIRTUAL)",
        "INSERT INTO t (a) VALUES (1), (NULL)",
    )
    t = profile_sqlite("g.db", tmp_path).tables[0]
    assert [c.name for c in t.columns] == ["a", "b"]
    assert t.columns[1].non_null_count == 1


def test_zero_byte_file_is_an_empty_database(tmp_path):
    (tmp_path / "e.db").write_bytes(b"")
    p = profile_sqlite("e.db", tmp_path)
    assert p.tables == ()


def test_database_with_no_user_tables(tmp_path):
    make_db(tmp_path / "n.db", "CREATE TABLE t (a)", "DROP TABLE t")
    assert profile_sqlite("n.db", tmp_path).tables == ()


def test_text_file_with_db_extension_is_invalid(tmp_path):
    (tmp_path / "x.db").write_text("this is not a database at all\n" * 10)
    with pytest.raises(InvalidDatabaseError):
        profile_sqlite("x.db", tmp_path)


def test_valid_header_but_corrupt_body_is_invalid(tmp_path):
    (tmp_path / "c.db").write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
    with pytest.raises(InvalidDatabaseError):
        profile_sqlite("c.db", tmp_path)


def test_truncated_database_is_invalid(users_db, tmp_path):
    good = users_db.read_bytes()
    (tmp_path / "t.db").write_bytes(good[:100])
    with pytest.raises(InvalidDatabaseError):
        profile_sqlite("t.db", tmp_path)


def test_missing_file_and_directory(tmp_path):
    with pytest.raises(InputAccessError) as ei:
        profile_sqlite("missing.db", tmp_path)
    assert str(tmp_path) not in str(ei.value)
    (tmp_path / "d.db").mkdir()
    with pytest.raises(InputAccessError):
        profile_sqlite("d.db", tmp_path)


@pytest.mark.parametrize("name", ["x.csv", "x.txt", "x", "x.db.bak"])
def test_unsupported_extension(tmp_path, name):
    (tmp_path / name).write_bytes(b"")
    with pytest.raises(UnsupportedFileTypeError):
        profile_sqlite(name, tmp_path)


def test_path_traversal_and_outside_base_rejected(users_db, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    with pytest.raises(PathAccessError):
        profile_sqlite("../app.db", base)
    with pytest.raises(PathAccessError):
        profile_sqlite("sub/../../app.db", base)
    with pytest.raises(PathAccessError):
        profile_sqlite(users_db, base)


def test_database_size_limit(users_db, tmp_path):
    with pytest.raises(InputTooLargeError, match="size limit"):
        profile_sqlite("app.db", tmp_path, IngestionLimits(max_db_bytes=100))


def test_table_limit(tmp_path):
    make_db(tmp_path / "t.db", "CREATE TABLE a (x)", "CREATE TABLE b (x)", "CREATE TABLE c (x)")
    assert len(profile_sqlite("t.db", tmp_path, IngestionLimits(max_tables=3)).tables) == 3
    with pytest.raises(InputTooLargeError, match="table limit"):
        profile_sqlite("t.db", tmp_path, IngestionLimits(max_tables=2))


def test_column_limit(tmp_path):
    make_db(tmp_path / "w.db", "CREATE TABLE w (a, b, c)")
    with pytest.raises(InputTooLargeError, match="column limit"):
        profile_sqlite("w.db", tmp_path, IngestionLimits(max_columns=2))


# ---------------------------------------------------------------- read-only enforcement


WRITE_ATTEMPTS = [
    "INSERT INTO users (id) VALUES (99)",
    "UPDATE users SET name = 'changed'",
    "DELETE FROM users",
    "CREATE TABLE evil (a)",
    "DROP TABLE users",
    "ALTER TABLE users ADD COLUMN extra TEXT",
    "CREATE INDEX idx_name ON users (name)",
]


@pytest.mark.parametrize("stmt", WRITE_ATTEMPTS)
def test_write_attempts_are_rejected_and_file_unchanged(users_db, tmp_path, stmt):
    before = snapshot(tmp_path)
    with closing(_open_readonly(users_db.resolve(), DEFAULT_LIMITS)) as conn:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute(stmt)
            conn.commit()
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("stmt", WRITE_ATTEMPTS)
def test_mode_ro_alone_blocks_writes_even_if_query_only_is_turned_off(users_db, tmp_path, stmt):
    before = snapshot(tmp_path)
    with closing(_open_readonly(users_db.resolve(), DEFAULT_LIMITS)) as conn:
        conn.execute("PRAGMA query_only = OFF")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute(stmt)
            conn.commit()
    assert snapshot(tmp_path) == before


def test_connection_is_hardened(users_db):
    with closing(_open_readonly(users_db.resolve(), DEFAULT_LIMITS)) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert conn.execute("PRAGMA trusted_schema").fetchone()[0] == 0
        with pytest.raises((sqlite3.OperationalError, sqlite3.NotSupportedError, AttributeError)):
            conn.load_extension("does-not-exist")


def test_profiling_does_not_modify_or_add_files(users_db, tmp_path):
    before = snapshot(tmp_path)
    profile_sqlite("app.db", tmp_path)
    assert snapshot(tmp_path) == before


# ---------------------------------------------------------------- URI construction


def test_uri_for_windows_drive_path_is_file_uri_with_ro():
    uri = _build_readonly_uri(PureWindowsPath(r"C:\Users\Me\My Data\a b#1.db"))
    assert uri == "file:///C:/Users/Me/My%20Data/a%20b%231.db?mode=ro"


def test_uri_for_unc_path_is_refused():
    with pytest.raises(InputAccessError, match="UNC"):
        _build_readonly_uri(PureWindowsPath(r"\\server\share\x.db"))


def test_uri_escapes_percent_hash_ampersand_and_unicode(tmp_path):
    name = "we ird #1 %20 & \u00e9.db"
    make_db(tmp_path / name, "CREATE TABLE t (a INTEGER)", "INSERT INTO t VALUES (1)")
    uri = _build_readonly_uri((tmp_path / name).resolve())
    assert uri.endswith("?mode=ro") and uri.count("?") == 1 and "#" not in uri
    assert "%2520" in uri  # a literal "%20" in the file name stays literal
    p = profile_sqlite(name, tmp_path)
    assert p.source_name == name and p.tables[0].row_count == 1


@pytest.mark.skipif(sys.platform == "win32", reason="'?' is not valid in Windows file names")
def test_question_mark_in_name_cannot_override_mode(tmp_path):
    name = "x?mode=rwc.db"
    make_db(tmp_path / name, "CREATE TABLE t (a)")
    uri = _build_readonly_uri((tmp_path / name).resolve())
    assert uri.count("?") == 1 and uri.endswith("?mode=ro")
    assert profile_sqlite(name, tmp_path).tables[0].name == "t"


# ---------------------------------------------------------------- identifier safety


def test_quote_identifier_escapes_quotes():
    assert quote_identifier("plain") == '"plain"'
    assert quote_identifier('we"ird') == '"we""ird"'
    assert quote_identifier("a b;--") == '"a b;--"'


@pytest.mark.parametrize("bad", ["", "a\nb", "a\x00b", "a\tb", "x" * 257, None, 5, "del\x7f"])
def test_quote_identifier_rejects_unusable_names(bad):
    with pytest.raises(InvalidDatabaseError):
        quote_identifier(bad)


def test_hostile_identifiers_are_handled_as_data(tmp_path):
    evil_table = 'evil"; DROP TABLE users; --'
    db = make_db(
        tmp_path / "h.db",
        "CREATE TABLE users (id INTEGER)",
        "INSERT INTO users VALUES (1)",
        'CREATE TABLE "evil""; DROP TABLE users; --" ("col""x" TEXT, "a b" INTEGER, "]`\'" TEXT)',
        'INSERT INTO "evil""; DROP TABLE users; --" VALUES (\'v\', 1, NULL)',
    )
    p = profile_sqlite("h.db", tmp_path)
    names = [t.name for t in p.tables]
    assert evil_table in names and "users" in names
    evil = next(t for t in p.tables if t.name == evil_table)
    assert [c.name for c in evil.columns] == ['col"x', "a b", "]`'"]
    assert evil.row_count == 1
    with closing(sqlite3.connect(db)) as conn:  # nothing was dropped
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_control_characters_in_table_name_are_rejected(tmp_path):
    make_db(tmp_path / "c.db", 'CREATE TABLE "bad\nname" (a)')
    with pytest.raises(InvalidDatabaseError, match="unusable"):
        profile_sqlite("c.db", tmp_path)


# ---------------------------------------------------------------- time budget


def test_time_budget_is_enforced(tmp_path):
    with closing(sqlite3.connect(tmp_path / "big.db")) as conn:
        conn.execute("CREATE TABLE t (a INTEGER, b TEXT)")
        conn.executemany("INSERT INTO t VALUES (?, ?)", ((i, "v") for i in range(30000)))
        conn.commit()
    with pytest.raises(DatabaseTimeoutError):
        profile_sqlite("big.db", tmp_path, IngestionLimits(sqlite_timeout_seconds=1e-9))


def test_locked_database_maps_to_timeout(tmp_path):
    db = make_db(tmp_path / "l.db", "CREATE TABLE t (a)", "INSERT INTO t VALUES (1)")
    locker = sqlite3.connect(db, isolation_level=None)
    try:
        locker.execute("BEGIN EXCLUSIVE")
        with pytest.raises(DatabaseTimeoutError):
            profile_sqlite("l.db", tmp_path, IngestionLimits(sqlite_timeout_seconds=0.2))
    finally:
        locker.close()
