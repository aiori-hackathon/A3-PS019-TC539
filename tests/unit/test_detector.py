import json
import logging

import pytest

from fieldledger.classes import load_class_registry
from fieldledger.detector import ambiguous_columns, detect_candidates
from fieldledger.errors import ConfigError
from fieldledger.findings import Finding
from fieldledger.profiles import (
    BroadType,
    ColumnProfile,
    SourceProfile,
    SourceType,
    TableProfile,
    TypeBasis,
)
from fieldledger.rules import load_rule_set

REGISTRY_TEXT = """\
taxonomy_status: provisional
required_class_count: 2
classes:
  - {id: ALPHA_1, label: Alpha}
  - {id: BETA_2, label: Beta}
"""

RULES_TEXT = """\
rules_status: provisional
rules:
  - {id: R_A_TOKEN, class_id: ALPHA_1, match: token, patterns: [alp, "alp mark"]}
  - {id: R_A_ALT, class_id: ALPHA_1, match: token, patterns: [alp]}
  - {id: R_A_EXACT, class_id: ALPHA_1, match: exact, patterns: [solo]}
  - {id: R_B_TOKEN, class_id: BETA_2, match: token, patterns: [bet]}
"""


def write(tmp_path, text, name):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def registry(tmp_path):
    return load_class_registry(write(tmp_path, REGISTRY_TEXT, "registry.yaml"))


@pytest.fixture
def ruleset(tmp_path, registry):
    return load_rule_set(write(tmp_path, RULES_TEXT, "rules.yaml"), registry)


def make_ruleset(tmp_path, registry, rules_block, name="r2.yaml"):
    return load_rule_set(
        write(tmp_path, "rules_status: provisional\nrules:\n" + rules_block, name), registry
    )


def make_profile(tables, source_name="s.csv", types=None):
    built = []
    for table_name, columns in tables.items():
        cols = tuple(
            ColumnProfile(
                name=c,
                position=i,
                inferred_type=(types or {}).get(c, BroadType.STRING),
                non_null_count=1,
                null_count=0,
            )
            for i, c in enumerate(columns, start=1)
        )
        built.append(TableProfile(name=table_name, columns=cols, row_count=1))
    return SourceProfile(SourceType.CSV, source_name, TypeBasis.OBSERVED_VALUES, tuple(built))


def classes_by_column(findings):
    out = {}
    for f in findings:
        out.setdefault(f.column_name, []).append(f.class_id)
    return out


def test_token_matching_is_whole_token_case_and_separator_insensitive(ruleset):
    p = make_profile({"t": ["alp_x", "ALP", "xAlp", "alp-1", "alpine", "alp2", "salp", "plain"]})
    got = classes_by_column(detect_candidates(p, ruleset, source_key="s.csv"))
    assert got == {
        "alp_x": ["ALPHA_1"],
        "ALP": ["ALPHA_1"],
        "xAlp": ["ALPHA_1"],
        "alp-1": ["ALPHA_1"],
    }


def test_exact_matching_requires_the_whole_name(ruleset):
    p = make_profile({"t": ["solo", "Solo", "SOLO!", "solo_x", "x_solo"]})
    got = classes_by_column(detect_candidates(p, ruleset, source_key="s.csv"))
    assert got == {"solo": ["ALPHA_1"], "Solo": ["ALPHA_1"], "SOLO!": ["ALPHA_1"]}


def test_multi_token_pattern_must_be_contiguous(tmp_path, registry):
    rs = make_ruleset(
        tmp_path, registry,
        "  - {id: R_PHRASE, class_id: ALPHA_1, match: token, patterns: ['one two']}\n",
    )
    p = make_profile({"t": ["x_one_two_y", "oneTwo", "one_x_two", "two_one"]})
    got = classes_by_column(detect_candidates(p, rs, source_key="s.csv"))
    assert set(got) == {"x_one_two_y", "oneTwo"}


def test_rule_ids_of_the_same_class_are_merged_and_sorted(ruleset):
    p = make_profile({"t": ["alp_mark"]})
    (f,) = detect_candidates(p, ruleset, source_key="s.csv")
    assert f.rule_ids == ("R_A_ALT", "R_A_TOKEN")


def test_ambiguous_columns_yield_one_finding_per_class_and_are_listed(ruleset):
    p = make_profile({"t": ["alp_bet", "only_alp", "only_bet"]})
    findings = detect_candidates(p, ruleset, source_key="s.csv")
    assert [(f.column_name, f.class_id) for f in findings] == [
        ("alp_bet", "ALPHA_1"),
        ("alp_bet", "BETA_2"),
        ("only_alp", "ALPHA_1"),
        ("only_bet", "BETA_2"),
    ]
    (amb,) = ambiguous_columns(findings)
    assert (amb.source_key, amb.table_name, amb.column_name) == ("s.csv", "t", "alp_bet")
    assert amb.class_ids == ("ALPHA_1", "BETA_2")


def test_no_ambiguity_means_empty_listing(ruleset):
    p = make_profile({"t": ["only_alp"]})
    assert ambiguous_columns(detect_candidates(p, ruleset, source_key="s.csv")) == ()


def test_no_match_gives_empty_tuple(ruleset):
    p = make_profile({"t": ["id", "name", "col_7"]})
    assert detect_candidates(p, ruleset, source_key="s.csv") == ()


def test_empty_profiles_and_tables(ruleset):
    assert detect_candidates(make_profile({}), ruleset, source_key="s.csv") == ()
    assert detect_candidates(make_profile({"t": []}), ruleset, source_key="s.csv") == ()


def test_findings_carry_exact_profile_identity(ruleset):
    p = make_profile({"People Table": ["id", "Alp Col"]})
    (f,) = detect_candidates(p, ruleset, source_key="dir/s.csv")
    assert isinstance(f, Finding)
    assert f.identity == ("dir/s.csv", "People Table", "Alp Col", "ALPHA_1")
    assert f.column_position == 2


def test_output_is_deterministic_and_independent_of_table_order(ruleset):
    forward = make_profile({"b_table": ["alp_1", "x"], "a_table": ["bet_1"]})
    backward = make_profile({"a_table": ["bet_1"], "b_table": ["alp_1", "x"]})
    one = detect_candidates(forward, ruleset, source_key="s.csv")
    two = detect_candidates(backward, ruleset, source_key="s.csv")
    again = detect_candidates(forward, ruleset, source_key="s.csv")
    assert one == two == again
    assert [f.table_name for f in one] == ["a_table", "b_table"]
    assert json.dumps([f.to_dict() for f in one]) == json.dumps([f.to_dict() for f in two])


def test_detection_ignores_column_types(ruleset):
    names = ["alp_a", "alp_b", "alp_c"]
    types = {"alp_a": BroadType.BINARY, "alp_b": BroadType.EMPTY, "alp_c": BroadType.UNKNOWN}
    p = make_profile({"t": names}, types=types)
    assert [f.column_name for f in detect_candidates(p, ruleset, source_key="s.csv")] == names


def test_same_basename_sources_stay_distinguishable(ruleset):
    p = make_profile({"people": ["alp_ref"]}, source_name="people.csv")
    fa = detect_candidates(p, ruleset, source_key="a/people.csv")
    fb = detect_candidates(p, ruleset, source_key="b/people.csv")
    assert fa[0].identity != fb[0].identity
    assert {f.source_key for f in fa + fb} == {"a/people.csv", "b/people.csv"}


@pytest.mark.parametrize("bad", ["", "/abs.csv", "../x.csv", "a\\b.csv"])
def test_invalid_source_key_is_rejected(ruleset, bad):
    with pytest.raises(ValueError):
        detect_candidates(make_profile({"t": ["alp"]}), ruleset, source_key=bad)


def test_detector_logs_counts_only(ruleset, caplog):
    p = make_profile({"secret_table": ["alp_secret_column"]})
    with caplog.at_level(logging.DEBUG, logger="fieldledger"):
        detect_candidates(p, ruleset, source_key="secret/dir.csv")
    assert "secret" not in caplog.text
    assert "detection: columns=1 findings=1" in caplog.text


def test_rules_referencing_unknown_classes_cannot_reach_the_detector(tmp_path, registry):
    bad = "  - {id: R_X, class_id: NOPE_9, match: token, patterns: [x1]}\n"
    with pytest.raises(ConfigError):
        make_ruleset(tmp_path, registry, bad)
