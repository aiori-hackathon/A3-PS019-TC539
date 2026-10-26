import sqlite3

import pytest

from fieldledger.discovery import discover_sources
from fieldledger.errors import InputAccessError, UnsupportedFileTypeError
from fieldledger.profiler import profile_source
from fieldledger.profiles import SourceType

def test_discovery_finds_supported_files_recursively(tmp_path):
    (tmp_path / "a.csv").write_text("id\n1\n", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.sqlite").touch()
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")

    found = discover_sources(tmp_path)

    assert [p.name for p in found] == ["a.csv", "b.sqlite"]


def test_discovery_non_recursive_only_finds_root_files(tmp_path):
    (tmp_path / "root.csv").write_text("id\n1\n", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "child.csv").write_text("id\n2\n", encoding="utf-8")

    found = discover_sources(tmp_path, recursive=False)

    assert [p.name for p in found] == ["root.csv"]


def test_discovery_rejects_missing_base(tmp_path):
    missing = tmp_path / "missing"

    with pytest.raises(InputAccessError):
        discover_sources(missing)


def test_unified_profiler_dispatches_csv(tmp_path):
    csv_file = tmp_path / "sample.csv"
    csv_file.write_text("id\n1\n2\n", encoding="utf-8")

    result = profile_source(csv_file.name, tmp_path)

    assert result.source_type == SourceType.CSV
    assert result.tables[0].row_count == 2


def test_unified_profiler_dispatches_sqlite(tmp_path):
    db_file = tmp_path / "sample.db"
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE records (id INTEGER)")
        conn.executemany("INSERT INTO records VALUES (?)", [(1,), (2,)])

    result = profile_source(db_file.name, tmp_path)

    assert result.source_type == SourceType.SQLITE
    assert result.tables[0].row_count == 2


def test_unified_profiler_rejects_unsupported_extension(tmp_path):
    (tmp_path / "notes.txt").write_text("ignore", encoding="utf-8")

    with pytest.raises(UnsupportedFileTypeError):
        profile_source("notes.txt", tmp_path)


def test_discovery_ignores_directory_symlinks(tmp_path):
    base = tmp_path / "input"
    base.mkdir()

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.csv").write_text("id\n1\n", encoding="utf-8")

    link = base / "linked_directory"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Directory symlinks are not available")

    assert discover_sources(base) == ()


def test_discovery_ignores_file_symlinks_escaping_base(tmp_path):
    base = tmp_path / "input"
    base.mkdir()

    outside = tmp_path / "outside.csv"
    outside.write_text("id\n1\n", encoding="utf-8")

    link = base / "outside.csv"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("File symlinks are not available")

    assert discover_sources(base) == ()


def test_discovery_returns_deterministic_order(tmp_path):
    (tmp_path / "z.csv").write_text("id\n1\n", encoding="utf-8")
    (tmp_path / "a.db").touch()
    (tmp_path / "m.sqlite").touch()

    found = discover_sources(tmp_path)

    assert [p.name for p in found] == ["a.db", "m.sqlite", "z.csv"]

def test_discovery_rejects_file_as_base(tmp_path):
    base_file = tmp_path / "not_a_directory"
    base_file.write_text("test", encoding="utf-8")

    with pytest.raises(InputAccessError):
        discover_sources(base_file)