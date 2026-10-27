import dataclasses
import json

import pytest

from fieldledger.errors import PathAccessError
from fieldledger.findings import (
    EVIDENCE_KIND,
    EVIDENCE_NOTICE,
    MAX_SOURCE_KEY_CHARS,
    Finding,
    source_key_for,
    validate_source_key,
)


def make(**overrides):
    kwargs = dict(
        source_key="a/people.csv",
        table_name="people",
        column_name="mobile_no",
        column_position=2,
        class_id="PERSONAL_IDENTITY",
        rule_ids=("R2", "R1"),
    )
    kwargs.update(overrides)
    return Finding(**kwargs)


def test_finding_normalises_rule_ids_and_exposes_identity():
    f = make(rule_ids=["R2", "R1", "R2"])
    assert f.rule_ids == ("R1", "R2")
    assert f.identity == ("a/people.csv", "people", "mobile_no", "PERSONAL_IDENTITY")
    assert f.column_identity == ("a/people.csv", "people", "mobile_no")
    assert f.evidence_kind == EVIDENCE_KIND


def test_to_dict_is_stable_and_json_serialisable():
    d = make().to_dict()
    assert list(d) == [
        "source_key", "table", "column", "position", "class_id", "rule_ids", "evidence_kind",
    ]
    assert d["rule_ids"] == ["R1", "R2"]
    assert json.loads(json.dumps(d)) == d


def test_finding_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        make().class_id = "X"  # type: ignore[misc]


@pytest.mark.parametrize(
    "overrides",
    [
        {"table_name": ""},
        {"column_name": ""},
        {"class_id": ""},
        {"table_name": None},
        {"column_position": 0},
        {"column_position": -1},
        {"column_position": True},
        {"column_position": 1.5},
        {"rule_ids": ()},
        {"rule_ids": "R1"},
        {"rule_ids": ("",)},
        {"evidence_kind": "legal_assessment"},
        {"source_key": "/abs.csv"},
    ],
)
def test_invalid_findings_are_rejected(overrides):
    with pytest.raises(ValueError):
        make(**overrides)


def test_findings_carry_no_legal_or_compliance_fields():
    forbidden = ("lawful", "legal", "complian", "consent", "violation", "verdict", "risk")
    names = [f.name for f in dataclasses.fields(Finding)] + list(make().to_dict())
    for name in names:
        assert not any(word in name.lower() for word in forbidden), name
    assert "not a legal determination" in EVIDENCE_NOTICE


@pytest.mark.parametrize("key", ["a.csv", "sub/a.csv", "sub/deeper/x y.db", "a..b.csv", ".hidden.csv"])
def test_valid_source_keys(key):
    assert validate_source_key(key) == key


@pytest.mark.parametrize(
    "key",
    [
        "", None, 5, "/abs/x.csv", "C:/x.csv", "c:x.csv", "a\\b.csv", "../x.csv", "a/../x.csv",
        "./x.csv", "a//b.csv", "a/", "x\ny.csv", "x\x00y.csv", "x" * (MAX_SOURCE_KEY_CHARS + 1),
    ],
)
def test_invalid_source_keys(key):
    with pytest.raises(ValueError):
        validate_source_key(key)


def test_source_key_for_is_relative_posix_and_distinguishes_same_basename(tmp_path):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "people.csv").write_text("x\n1\n", encoding="utf-8")
    ka = source_key_for(tmp_path / "a" / "people.csv", tmp_path)
    kb = source_key_for("b/people.csv", tmp_path)
    assert (ka, kb) == ("a/people.csv", "b/people.csv")
    assert ka != kb


def test_source_key_for_accepts_nested_relative_and_absolute_forms(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.csv").write_text("a\n1\n", encoding="utf-8")
    assert source_key_for("sub/x.csv", tmp_path) == "sub/x.csv"
    assert source_key_for(tmp_path / "sub" / "x.csv", tmp_path) == "sub/x.csv"
    assert source_key_for("sub/../sub/x.csv", tmp_path) == "sub/x.csv"


def test_source_key_for_rejects_escapes_and_the_base_itself(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (tmp_path / "outside.csv").write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(PathAccessError):
        source_key_for("../outside.csv", base)
    with pytest.raises(PathAccessError):
        source_key_for(tmp_path / "outside.csv", base)
    with pytest.raises(ValueError):
        source_key_for(".", base)
