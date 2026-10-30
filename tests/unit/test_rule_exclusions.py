"""exclude_patterns: loader validation and detector semantics (detection-quality work)."""

from pathlib import Path

import pytest

from fieldledger.classes import load_class_registry
from fieldledger.detector import detect_candidates, explain_column
from fieldledger.errors import ConfigError
from fieldledger.profiles import (
    BroadType,
    ColumnProfile,
    SourceProfile,
    SourceType,
    TableProfile,
    TypeBasis,
)
from fieldledger.rules import MIN_EXCLUDE_TOKENS, load_rule_set

REGISTRY_TEXT = """\
taxonomy_status: provisional
required_class_count: 2
classes:
  - {id: ALPHA_1, label: Alpha}
  - {id: BETA_2, label: Beta}
"""


def write(tmp_path, text, name="rules.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def registry(tmp_path):
    return load_class_registry(write(tmp_path, REGISTRY_TEXT, "registry.yaml"))


def rules_doc(*rule_blocks):
    return "rules_status: provisional\nrules:\n" + "".join(rule_blocks)


def rule_block(rule_id="RULE_A", class_id="ALPHA_1", match="token", patterns="[phone]", extra=""):
    return (
        f"  - id: {rule_id}\n    class_id: {class_id}\n    match: {match}\n"
        f"    patterns: {patterns}\n{extra}"
    )


def profile_for(*names):
    cols = tuple(ColumnProfile(n, i + 1, BroadType.STRING, 1, 0) for i, n in enumerate(names))
    return SourceProfile(
        SourceType.CSV, "s.csv", TypeBasis.OBSERVED_VALUES, (TableProfile("t", cols, 1),)
    )


def detect(ruleset, *names):
    return detect_candidates(profile_for(*names), ruleset, source_key="s.csv")


# ------------------------------------------------------------------ loader


def test_exclude_patterns_are_optional_and_default_to_empty(tmp_path, registry):
    rs = load_rule_set(write(tmp_path, rules_doc(rule_block())), registry)
    assert rs.rules[0].exclude_patterns == ()


def test_exclude_patterns_are_normalised_deduplicated_and_sorted(tmp_path, registry):
    extra = "    exclude_patterns: [Phone Model, phone_model, phone OS]\n"
    rs = load_rule_set(write(tmp_path, rules_doc(rule_block(extra=extra))), registry)
    assert rs.rules[0].exclude_patterns == (("phone", "model"), ("phone", "os"))


@pytest.mark.parametrize("bad", ["[count]", "[phone]", "[Count]"])
def test_single_word_exclusions_are_rejected_as_too_broad(tmp_path, registry, bad):
    assert MIN_EXCLUDE_TOKENS == 2
    extra = f"    exclude_patterns: {bad}\n"
    with pytest.raises(ConfigError, match="at least 2 tokens"):
        load_rule_set(write(tmp_path, rules_doc(rule_block(extra=extra))), registry)


def test_exclusion_may_not_repeat_one_of_the_rules_own_patterns(tmp_path, registry):
    block = rule_block(patterns="[phone, phone model]", extra="    exclude_patterns: [phone model]\n")
    with pytest.raises(ConfigError, match="must not repeat"):
        load_rule_set(write(tmp_path, rules_doc(block)), registry)


@pytest.mark.parametrize("bad", ["[]", "phone model", "[1, 2]", "{a: b}", "[x1 y1, 5]"])
def test_malformed_exclude_patterns_are_rejected(tmp_path, registry, bad):
    extra = f"    exclude_patterns: {bad}\n"
    with pytest.raises(ConfigError):
        load_rule_set(write(tmp_path, rules_doc(rule_block(extra=extra))), registry)


def test_unknown_rule_keys_are_still_rejected(tmp_path, registry):
    with pytest.raises(ConfigError, match="unknown keys"):
        load_rule_set(write(tmp_path, rules_doc(rule_block(extra="    excludes: [a b]\n"))), registry)


def test_exclusion_error_messages_do_not_echo_the_pattern(tmp_path, registry):
    extra = "    exclude_patterns: [secret]\n"
    with pytest.raises(ConfigError) as info:
        load_rule_set(write(tmp_path, rules_doc(rule_block(extra=extra))), registry)
    assert "secret" not in str(info.value)


def test_a_rule_file_without_exclusions_still_loads_unchanged():
    """The baseline (d3ab9c2) rules.yaml has no exclude_patterns: the old format stays valid."""
    repo = Path(__file__).resolve().parents[2]
    real = load_class_registry(repo / "config" / "classes.yaml")
    baseline = load_rule_set(repo / "tests" / "fixtures" / "rules_baseline_d3ab9c2.yaml", real)
    assert all(r.exclude_patterns == () for r in baseline.rules)
    assert len(baseline.rules) == 19


# ------------------------------------------------------------------ detector semantics


@pytest.fixture
def ruleset(tmp_path, registry):
    rules = (
        rule_block("RULE_PHONE", "ALPHA_1", patterns="[phone]",
                   extra="    exclude_patterns: [phone model, phone call]\n")
        + rule_block("RULE_DEVICE", "BETA_2", patterns="[model]")
    )
    return load_rule_set(write(tmp_path, rules_doc(rules)), registry)


def test_excluded_rule_does_not_fire(ruleset):
    classes = {f.class_id for f in detect(ruleset, "phone_model")}
    assert classes == {"BETA_2"}  # RULE_PHONE suppressed, RULE_DEVICE unaffected


def test_exclusion_only_suppresses_its_own_rule_not_other_rules(ruleset):
    (f,) = detect(ruleset, "phone_model")
    assert f.rule_ids == ("RULE_DEVICE",)


def test_exclusion_needs_the_whole_phrase_as_a_contiguous_run(ruleset):
    # "phone" and "model" are both present but not adjacent, so the exclusion does not apply
    classes = {f.class_id for f in detect(ruleset, "phone_x_model")}
    assert classes == {"ALPHA_1", "BETA_2"}
    assert {f.class_id for f in detect(ruleset, "phone_number")} == {"ALPHA_1"}


def test_exclusion_works_inside_a_longer_name(ruleset):
    assert {f.class_id for f in detect(ruleset, "primary_phone_call_count")} == set()


def test_explain_column_reports_suppression_and_matching_pattern(ruleset):
    by_rule = {m.rule_id: m for m in explain_column("phone_model", ruleset)}
    assert by_rule["RULE_PHONE"].suppressed
    assert by_rule["RULE_PHONE"].excluded_by == (("phone", "model"),)
    assert by_rule["RULE_PHONE"].matched_patterns == (("phone",),)
    assert not by_rule["RULE_DEVICE"].suppressed
    assert by_rule["RULE_DEVICE"].matched_patterns == (("model",),)


def test_explain_column_agrees_with_detect_candidates(ruleset):
    for name in ("phone", "phone_model", "model", "phone_call", "x"):
        expected = {
            (m.class_id, m.rule_id) for m in explain_column(name, ruleset) if not m.suppressed
        }
        got = {(f.class_id, r) for f in detect(ruleset, name) for r in f.rule_ids}
        assert got == expected, name


def test_exact_rules_support_exclusions_too(tmp_path, registry):
    block = rule_block("RULE_E", match="exact", patterns="[pin]", extra="    exclude_patterns: [pin pin]\n")
    rs = load_rule_set(write(tmp_path, rules_doc(block)), registry)
    assert {f.class_id for f in detect(rs, "pin")} == {"ALPHA_1"}
    assert explain_column("pin_pin", rs) == ()  # exact rule does not match; nothing to suppress
