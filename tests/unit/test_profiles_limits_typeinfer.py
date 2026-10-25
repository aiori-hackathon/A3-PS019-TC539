import dataclasses
import json

import pytest

from fieldledger.errors import ConfigError
from fieldledger.limits import DEFAULT_LIMITS, IngestionLimits
from fieldledger.profiles import (
    PROFILE_SCHEMA_VERSION,
    BroadType,
    ColumnProfile,
    SourceProfile,
    SourceType,
    TableProfile,
    TypeBasis,
)
from fieldledger.typeinfer import ColumnTypeAccumulator, broad_type_from_declared


def infer(*cells):
    acc = ColumnTypeAccumulator()
    for c in cells:
        acc.observe(c)
    return acc


@pytest.mark.parametrize(
    "cells, expected",
    [
        (["1", "22", "-3", "+4", "0"], BroadType.INTEGER),
        (["1", "2.5", "1e3", ".5"], BroadType.FLOAT),
        (["007", "012"], BroadType.STRING),  # leading zeros: identifier-like, not numeric
        (["1,000"], BroadType.STRING),  # no thousands separators
        (["nan", "inf"], BroadType.STRING),
        (["true", "FALSE", "Yes", "no"], BroadType.BOOLEAN),
        (["0", "1"], BroadType.INTEGER),  # 0/1 are integers, not booleans
        (["true", "1"], BroadType.STRING),
        (["2024-01-31", "1999-12-01"], BroadType.DATE),
        (["2024-02-30"], BroadType.STRING),  # impossible date
        (["2024-01-31T10:20", "2024-01-31 10:20:30.123456", "2024-01-31T10:20:30Z",
          "2024-01-31T10:20:30+05:30"], BroadType.DATETIME),
        (["2024-01-31T25:00"], BroadType.STRING),
        (["2024-01-31T10:20:30+25:00"], BroadType.STRING),
        (["2024-01-31", "2024-01-31T10:20"], BroadType.STRING),  # mixed date/datetime
        (["abc", "1"], BroadType.STRING),
        (["\u0661\u0662"], BroadType.STRING),  # non-ASCII digits are not integers
        (["", "   "], BroadType.EMPTY),
        ([], BroadType.EMPTY),
    ],
)
def test_csv_type_inference(cells, expected):
    assert infer(*cells).result() is expected


def test_null_counting_is_blank_only():
    acc = infer("a", "", "  ", "NULL", "N/A", "\t")
    assert acc.non_null == 3  # "a", "NULL", "N/A" are values, not nulls
    assert acc.null == 3


@pytest.mark.parametrize(
    "declared, expected",
    [
        ("INTEGER", BroadType.INTEGER),
        ("bigint", BroadType.INTEGER),
        ("POINT", BroadType.INTEGER),  # documented SQLite affinity quirk
        ("VARCHAR(20)", BroadType.STRING),
        ("TEXT", BroadType.STRING),
        ("CLOB", BroadType.STRING),
        ("BLOB", BroadType.BINARY),
        ("REAL", BroadType.FLOAT),
        ("DOUBLE PRECISION", BroadType.FLOAT),
        ("FLOAT", BroadType.FLOAT),
        ("NUMERIC", BroadType.NUMERIC),
        ("DECIMAL(10,2)", BroadType.NUMERIC),
        ("BOOLEAN", BroadType.BOOLEAN),
        ("DATE", BroadType.DATE),
        ("DATETIME", BroadType.DATETIME),
        ("TIMESTAMP", BroadType.DATETIME),
        ("", BroadType.UNKNOWN),
        (None, BroadType.UNKNOWN),
    ],
)
def test_declared_type_mapping(declared, expected):
    assert broad_type_from_declared(declared) is expected


def test_default_limits_are_documented_values():
    d = DEFAULT_LIMITS
    assert d.max_file_bytes == 50 * 1024 * 1024
    assert d.max_rows == 100_000
    assert d.max_columns == 1_000
    assert d.max_line_chars == 262_144
    assert d.max_db_bytes == 256 * 1024 * 1024
    assert d.max_tables == 1_000
    assert d.sqlite_timeout_seconds == 30.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_rows": 0},
        {"max_rows": -1},
        {"max_rows": True},
        {"max_columns": 1.5},
        {"max_file_bytes": "10"},
        {"max_tables": 0},
        {"sqlite_timeout_seconds": 0},
        {"sqlite_timeout_seconds": -1.0},
        {"sqlite_timeout_seconds": True},
    ],
)
def test_invalid_limits_rejected(kwargs):
    with pytest.raises(ConfigError):
        IngestionLimits(**kwargs)


def test_limits_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_LIMITS.max_rows = 1  # type: ignore[misc]


def test_profile_to_dict_is_stable_and_json_serializable():
    col = ColumnProfile("a", 1, BroadType.INTEGER, non_null_count=2, null_count=1)
    tbl = TableProfile("t", [col], row_count=3)
    src = SourceProfile(SourceType.CSV, "t.csv", TypeBasis.OBSERVED_VALUES, [tbl])
    d = src.to_dict()
    assert list(d) == ["schema_version", "source_type", "source_name", "type_basis", "tables"]
    assert d["schema_version"] == PROFILE_SCHEMA_VERSION
    assert list(d["tables"][0]) == ["name", "row_count", "columns"]
    assert list(d["tables"][0]["columns"][0]) == [
        "name", "position", "inferred_type", "declared_type", "non_null_count", "null_count",
    ]
    assert json.loads(json.dumps(d)) == d
    assert isinstance(src.tables, tuple) and isinstance(tbl.columns, tuple)


def test_profiles_are_frozen():
    col = ColumnProfile("a", 1, BroadType.STRING)
    with pytest.raises(dataclasses.FrozenInstanceError):
        col.name = "b"  # type: ignore[misc]


def test_counts_must_be_paired_and_non_negative():
    with pytest.raises(ValueError):
        ColumnProfile("a", 1, BroadType.STRING, non_null_count=1)
    with pytest.raises(ValueError):
        ColumnProfile("a", 1, BroadType.STRING, non_null_count=-1, null_count=0)
