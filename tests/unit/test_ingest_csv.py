import hashlib
import json
import logging

import pytest

import fieldledger.ingest_csv as ingest_csv
from fieldledger.errors import (
    ConfigError,
    InputAccessError,
    InputTooLargeError,
    MalformedCsvError,
    PathAccessError,
    UnsupportedFileTypeError,
)
from fieldledger.ingest_csv import profile_csv
from fieldledger.limits import IngestionLimits
from fieldledger.profiles import BroadType, SourceType, TypeBasis

SECRET = "zz-synthetic-sentinel-7781"


def write(tmp_path, name, content, *, binary=False):
    p = tmp_path / name
    if binary or isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_bytes(content.encode("utf-8"))
    return p


def cols(profile):
    return {c.name: c for c in profile.tables[0].columns}


NORMAL = (
    "id,name,score,active,joined,seen_at,empty_col\n"
    f"1,{SECRET}-a,1.5,true,2024-01-02,2024-01-02T10:00:00,\n"
    f"2,{SECRET}-b,2,no,2024-02-03,2024-02-03 11:30,\n"
    "3,,,yes,2024-03-04,2024-03-04T12:00:00Z,\n"
)


def test_normal_csv_profile(tmp_path):
    write(tmp_path, "people.csv", NORMAL)
    p = profile_csv("people.csv", tmp_path)
    assert p.source_type is SourceType.CSV
    assert p.type_basis is TypeBasis.OBSERVED_VALUES
    assert p.source_name == "people.csv"
    (t,) = p.tables
    assert t.name == "people" and t.row_count == 3
    assert [c.name for c in t.columns] == [
        "id", "name", "score", "active", "joined", "seen_at", "empty_col",
    ]
    assert [c.position for c in t.columns] == [1, 2, 3, 4, 5, 6, 7]
    c = cols(p)
    assert c["id"].inferred_type is BroadType.INTEGER
    assert c["name"].inferred_type is BroadType.STRING
    assert (c["name"].non_null_count, c["name"].null_count) == (2, 1)
    assert c["score"].inferred_type is BroadType.FLOAT
    assert (c["score"].non_null_count, c["score"].null_count) == (2, 1)
    assert c["active"].inferred_type is BroadType.BOOLEAN
    assert c["joined"].inferred_type is BroadType.DATE
    assert c["seen_at"].inferred_type is BroadType.DATETIME
    assert c["empty_col"].inferred_type is BroadType.EMPTY
    assert (c["empty_col"].non_null_count, c["empty_col"].null_count) == (0, 3)
    assert all(col.declared_type is None for col in t.columns)


def test_profile_contains_no_raw_cell_values(tmp_path):
    write(tmp_path, "people.csv", NORMAL)
    p = profile_csv("people.csv", tmp_path)
    assert SECRET not in json.dumps(p.to_dict())
    assert SECRET not in repr(p)
    assert str(tmp_path) not in json.dumps(p.to_dict())


def test_logging_never_contains_cell_values(tmp_path, caplog):
    write(tmp_path, "people.csv", NORMAL)
    with caplog.at_level(logging.DEBUG, logger="fieldledger"):
        profile_csv("people.csv", tmp_path)
    assert SECRET not in caplog.text


def test_profile_is_deterministic(tmp_path):
    write(tmp_path, "people.csv", NORMAL)
    a = profile_csv("people.csv", tmp_path)
    b = profile_csv("people.csv", tmp_path)
    assert a == b
    assert json.dumps(a.to_dict()) == json.dumps(b.to_dict())


def test_input_file_is_not_modified(tmp_path):
    p = write(tmp_path, "people.csv", NORMAL)
    before = (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
    profile_csv("people.csv", tmp_path)
    assert (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns) == before
    assert sorted(x.name for x in tmp_path.iterdir()) == ["people.csv"]


def test_utf8_bom_is_stripped_from_first_header(tmp_path):
    write(tmp_path, "b.csv", b"\xef\xbb\xbfid,name\n1,x\n")
    p = profile_csv("b.csv", tmp_path)
    assert [c.name for c in p.tables[0].columns] == ["id", "name"]


def test_plain_utf8_with_non_ascii_header(tmp_path):
    write(tmp_path, "u.csv", "id,caf\u00e9\n1,x\n")
    assert [c.name for c in profile_csv("u.csv", tmp_path).tables[0].columns] == ["id", "caf\u00e9"]


def test_invalid_utf8_is_rejected_not_guessed(tmp_path):
    write(tmp_path, "l.csv", b"id,name\n1,caf\xe9\n")
    with pytest.raises(MalformedCsvError, match="not valid utf-8-sig"):
        profile_csv("l.csv", tmp_path)


def test_explicit_fallback_encoding_is_opt_in(tmp_path):
    write(tmp_path, "l.csv", b"id,name\n1,caf\xe9\n")
    p = profile_csv("l.csv", tmp_path, encoding="cp1252")
    assert p.tables[0].row_count == 1


def test_unknown_encoding_option_rejected(tmp_path):
    write(tmp_path, "a.csv", "a\n1\n")
    with pytest.raises(ConfigError):
        profile_csv("a.csv", tmp_path, encoding="utf-7")


@pytest.mark.parametrize("content", [b"", b"\xef\xbb\xbf", b"\n\n\n", b"\r\n\r\n"])
def test_empty_or_headerless_file_rejected(tmp_path, content):
    write(tmp_path, "e.csv", content)
    with pytest.raises(MalformedCsvError, match="empty or has no header"):
        profile_csv("e.csv", tmp_path)


def test_whitespace_only_header_row_rejected_as_blank(tmp_path):
    write(tmp_path, "e.csv", "  \n1\n")
    with pytest.raises(MalformedCsvError, match="blank header"):
        profile_csv("e.csv", tmp_path)


def test_header_only_file_is_valid_with_zero_rows(tmp_path):
    write(tmp_path, "h.csv", "a,b\n")
    p = profile_csv("h.csv", tmp_path)
    assert p.tables[0].row_count == 0
    assert all(c.inferred_type is BroadType.EMPTY for c in p.tables[0].columns)
    assert all((c.non_null_count, c.null_count) == (0, 0) for c in p.tables[0].columns)


def test_leading_blank_lines_before_header_are_skipped(tmp_path):
    write(tmp_path, "h.csv", "\n\na,b\n1,2\n")
    assert [c.name for c in profile_csv("h.csv", tmp_path).tables[0].columns] == ["a", "b"]


@pytest.mark.parametrize("header", ["a,,c", "a, ,c", ",a", "a,"])
def test_blank_header_rejected(tmp_path, header):
    write(tmp_path, "x.csv", header + "\n1,2,3\n")
    with pytest.raises(MalformedCsvError, match="blank header"):
        profile_csv("x.csv", tmp_path)


@pytest.mark.parametrize("header", ["a,b,a", "ID,id", "a, a ", "Name,b,NAME"])
def test_duplicate_headers_rejected(tmp_path, header):
    write(tmp_path, "x.csv", header + "\n1,2,3\n")
    with pytest.raises(MalformedCsvError, match="duplicate header"):
        profile_csv("x.csv", tmp_path)


def test_header_names_are_stripped(tmp_path):
    write(tmp_path, "x.csv", " a , b \n1,2\n")
    assert [c.name for c in profile_csv("x.csv", tmp_path).tables[0].columns] == ["a", "b"]


def test_short_row_rejected_with_line_number_only(tmp_path):
    write(tmp_path, "x.csv", f"a,b,c\n1,2,3\n4,{SECRET}\n")
    with pytest.raises(MalformedCsvError, match=r"line 3: expected 3 fields, found 2") as ei:
        profile_csv("x.csv", tmp_path)
    assert SECRET not in str(ei.value)
    assert str(tmp_path) not in str(ei.value)


def test_long_row_rejected(tmp_path):
    write(tmp_path, "x.csv", "a,b\n1,2,3\n")
    with pytest.raises(MalformedCsvError, match="expected 2 fields, found 3"):
        profile_csv("x.csv", tmp_path)


def test_unterminated_quote_rejected(tmp_path):
    write(tmp_path, "x.csv", 'a,b\n1,"never closed\n2,3\n')
    with pytest.raises(MalformedCsvError):
        profile_csv("x.csv", tmp_path)


def test_garbage_after_closing_quote_rejected(tmp_path):
    write(tmp_path, "x.csv", 'a,b\n"x"y,2\n')
    with pytest.raises(MalformedCsvError):
        profile_csv("x.csv", tmp_path)


def test_nul_byte_rejected(tmp_path):
    write(tmp_path, "x.csv", b"a,b\n1,2\x00\n")
    with pytest.raises(MalformedCsvError, match="NUL"):
        profile_csv("x.csv", tmp_path)


def test_quoted_embedded_newline_and_comma_is_one_row(tmp_path):
    write(tmp_path, "x.csv", 'a,b\n1,"line one\nline two, with comma"\n2,plain\n')
    p = profile_csv("x.csv", tmp_path)
    assert p.tables[0].row_count == 2


def test_blank_lines_between_rows_are_skipped_not_counted(tmp_path):
    write(tmp_path, "x.csv", "a,b\n1,2\n\n3,4\n\n")
    assert profile_csv("x.csv", tmp_path).tables[0].row_count == 2


def test_all_empty_row_is_a_real_row_of_nulls(tmp_path):
    write(tmp_path, "x.csv", "a,b\n,\n1,2\n")
    p = profile_csv("x.csv", tmp_path)
    assert p.tables[0].row_count == 2
    assert cols(p)["a"].null_count == 1


def test_crlf_and_cr_line_endings(tmp_path):
    write(tmp_path, "x.csv", b"a,b\r\n1,2\r\n3,4\r\n")
    assert profile_csv("x.csv", tmp_path).tables[0].row_count == 2


def test_literal_null_text_is_not_treated_as_null(tmp_path):
    write(tmp_path, "x.csv", "a\nNULL\nN/A\n\"\"\n  \n")
    c = cols(profile_csv("x.csv", tmp_path))["a"]
    assert (c.non_null_count, c.null_count) == (2, 2)


def test_row_limit(tmp_path):
    lim = IngestionLimits(max_rows=3)
    write(tmp_path, "ok.csv", "a\n1\n2\n3\n")
    assert profile_csv("ok.csv", tmp_path, lim).tables[0].row_count == 3
    write(tmp_path, "big.csv", "a\n1\n2\n3\n4\n")
    with pytest.raises(InputTooLargeError, match="row limit"):
        profile_csv("big.csv", tmp_path, lim)


def test_column_limit(tmp_path):
    write(tmp_path, "w.csv", "a,b,c\n1,2,3\n")
    with pytest.raises(InputTooLargeError, match="column limit"):
        profile_csv("w.csv", tmp_path, IngestionLimits(max_columns=2))


def test_file_size_limit_checked_before_reading(tmp_path):
    write(tmp_path, "big.csv", "a\n" + "1\n" * 50)
    with pytest.raises(InputTooLargeError, match="size limit"):
        profile_csv("big.csv", tmp_path, IngestionLimits(max_file_bytes=20))


def test_file_size_limit_enforced_while_reading(tmp_path, monkeypatch):
    write(tmp_path, "grow.csv", "a\n" + "1\n" * 50)
    monkeypatch.setattr(ingest_csv, "regular_file_size", lambda _p: 0)  # simulate growth
    with pytest.raises(InputTooLargeError, match="size limit"):
        profile_csv("grow.csv", tmp_path, IngestionLimits(max_file_bytes=20))


def test_over_long_line_rejected(tmp_path):
    write(tmp_path, "x.csv", "a\n" + "x" * 500 + "\n")
    with pytest.raises(MalformedCsvError, match="line length"):
        profile_csv("x.csv", tmp_path, IngestionLimits(max_line_chars=100))


def test_traversal_and_outside_paths_rejected(tmp_path, tmp_path_factory):
    base = tmp_path / "base"
    base.mkdir()
    write(tmp_path, "outside.csv", "a\n1\n")
    with pytest.raises(PathAccessError):
        profile_csv("../outside.csv", base)
    with pytest.raises(PathAccessError):
        profile_csv("sub/../../outside.csv", base)
    with pytest.raises(PathAccessError):
        profile_csv(tmp_path / "outside.csv", base)
    other = tmp_path_factory.mktemp("other")
    write(other, "o.csv", "a\n1\n")
    with pytest.raises(PathAccessError):
        profile_csv(other / "o.csv", base)


def test_nested_relative_and_absolute_inside_base_allowed(tmp_path):
    (tmp_path / "sub").mkdir()
    write(tmp_path / "sub", "x.csv", "a\n1\n")
    assert profile_csv("sub/x.csv", tmp_path).tables[0].row_count == 1
    assert profile_csv(tmp_path / "sub" / "x.csv", tmp_path).tables[0].row_count == 1


def test_path_error_message_has_no_path(tmp_path):
    with pytest.raises(PathAccessError) as ei:
        profile_csv("../x.csv", tmp_path)
    assert str(tmp_path) not in str(ei.value)


@pytest.mark.parametrize("name", ["x.txt", "x.csv.txt", "x", "x.xlsx", "x.db"])
def test_unsupported_extension_rejected(tmp_path, name):
    write(tmp_path, name, "a\n1\n")
    with pytest.raises(UnsupportedFileTypeError):
        profile_csv(name, tmp_path)


def test_uppercase_extension_accepted(tmp_path):
    write(tmp_path, "X.CSV", "a\n1\n")
    assert profile_csv("X.CSV", tmp_path).tables[0].row_count == 1


def test_missing_file_and_directory_rejected(tmp_path):
    with pytest.raises(InputAccessError) as ei:
        profile_csv("nope.csv", tmp_path)
    assert str(tmp_path) not in str(ei.value)
    (tmp_path / "dir.csv").mkdir()
    with pytest.raises(InputAccessError):
        profile_csv("dir.csv", tmp_path)
