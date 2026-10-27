import dataclasses
from pathlib import Path

import pytest

from fieldledger import rules as rules_module
from fieldledger.classes import load_class_registry
from fieldledger.errors import ConfigError
from fieldledger.rules import (
    MAX_PATTERN_CHARS,
    load_rule_set,
    tokenize_name,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

REGISTRY_TEXT = """\
taxonomy_status: {status}
required_class_count: 2
classes:
  - {{id: ALPHA_1, label: Alpha}}
  - {{id: BETA_2, label: Beta}}
"""

RULE = "  - id: RULE_A\n    class_id: ALPHA_1\n    match: token\n    patterns: [x1]\n"


def doc(rule_lines, status="provisional"):
    return f"rules_status: {status}\nrules:\n{rule_lines}"


def write(tmp_path, text, name="rules.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def registry(tmp_path):
    p = write(tmp_path, REGISTRY_TEXT.format(status="provisional"), "registry.yaml")
    return load_class_registry(p)


@pytest.fixture
def confirmed_registry(tmp_path):
    p = write(tmp_path, REGISTRY_TEXT.format(status="confirmed"), "registry_confirmed.yaml")
    return load_class_registry(p)


# ------------------------------------------------------------------ tokenisation


@pytest.mark.parametrize(
    "name, expected",
    [
        ("tc1_alpha", ("tc1", "alpha")),
        ("TC2Beta", ("tc2", "beta")),
        ("HTTPServer", ("http", "server")),
        ("fooBar2Baz", ("foo", "bar2", "baz")),
        ("a-b c.d", ("a", "b", "c", "d")),
        ("ALL_CAPS_NAME", ("all", "caps", "name")),
        ("tc10", ("tc10",)),  # letters and digits are not split
        ("\u00c9COLE", ("\u00e9cole",)),  # casefolded, non-ASCII letters kept
        ("\uff34\uff23\uff11", ("tc1",)),  # NFKC: full-width characters
        ("", ()),
        ("___", ()),
        ("  ", ()),
    ],
)
def test_tokenize_name(name, expected):
    assert tokenize_name(name) == expected


# ------------------------------------------------------------------ loading


VALID = """\
rules_status: provisional
rules:
  - id: RULE_A
    class_id: ALPHA_1
    match: token
    patterns: [alpha_marker, "alpha two"]
  - id: RULE_B
    class_id: BETA_2
    match: exact
    patterns: [beta]
"""


def test_valid_rules_load(tmp_path, registry):
    rs = load_rule_set(write(tmp_path, VALID), registry)
    assert rs.is_provisional is True
    assert rs.rules_status == "provisional"
    assert rs.rule_ids() == ("RULE_A", "RULE_B")
    a, b = rs.rules
    assert (a.class_id, a.match) == ("ALPHA_1", "token")
    assert a.patterns == (("alpha", "marker"), ("alpha", "two"))
    assert (b.class_id, b.match, b.patterns) == ("BETA_2", "exact", (("beta",),))


def test_patterns_are_normalised_deduplicated_and_sorted(tmp_path, registry):
    text = doc(RULE.replace("[x1]", '[TC1, tc1, "tc1 ", Tc1, Zeta, alpha]'))
    (rule,) = load_rule_set(write(tmp_path, text), registry).rules
    assert rule.patterns == (("alpha",), ("tc1",), ("zeta",))


def test_loaded_rules_are_immutable(tmp_path, registry):
    rs = load_rule_set(write(tmp_path, VALID), registry)
    with pytest.raises(dataclasses.FrozenInstanceError):
        rs.rules_status = "confirmed"  # type: ignore[misc]
    assert isinstance(rs.rules, tuple) and isinstance(rs.rules[0].patterns, tuple)


def test_confirmed_rules_with_confirmed_taxonomy_are_allowed(tmp_path, confirmed_registry):
    rs = load_rule_set(write(tmp_path, doc(RULE, "confirmed")), confirmed_registry)
    assert rs.is_provisional is False


def test_confirmed_rules_with_provisional_taxonomy_are_rejected(tmp_path, registry):
    with pytest.raises(ConfigError, match="provisional class taxonomy"):
        load_rule_set(write(tmp_path, doc(RULE, "confirmed")), registry)


@pytest.mark.parametrize(
    "text, match",
    [
        (doc(RULE, "draft"), "rules_status"),
        ("rules:\n" + RULE, "rules_status"),
        (doc(RULE) + "extra: 1\n", "unknown top-level"),
        ("rules_status: provisional\nrules: []\n", "non-empty"),
        ("rules_status: provisional\nrules: nope\n", "non-empty"),
        (doc("  - just text\n"), "must be a mapping"),
        (doc(RULE + "    colour: red\n"), "unknown keys"),
        (doc(RULE.replace("    match: token\n", "")), "missing keys"),
        (doc(RULE.replace("RULE_A", "1bad")), r"\.id"),
        (doc(RULE + RULE.replace("RULE_A", "rule_a")), "duplicate rule id"),
        (doc(RULE.replace("ALPHA_1", "GAMMA_9")), "class_id"),
        (doc(RULE.replace("ALPHA_1", "alpha_1")), "class_id"),  # exact, case-sensitive
        (doc(RULE.replace("token", "regex")), r"\.match"),
        (doc(RULE.replace("[x1]", "[]")), "patterns must be a non-empty list"),
        (doc(RULE.replace("[x1]", "x1")), "patterns must be a non-empty list"),
        (doc(RULE.replace("[x1]", "[no]")), "must be a string"),  # YAML boolean
        (doc(RULE.replace("[x1]", "[5]")), "must be a string"),
        (doc(RULE.replace("[x1]", '["___"]')), "no usable tokens"),
        (doc(RULE.replace("[x1]", '["a b c d e f g h i"]')), "too many tokens"),
        (doc(RULE.replace("[x1]", '["' + "x" * (MAX_PATTERN_CHARS + 1) + '"]')), "too long"),
        ("rules_status: provisional\nrules:\n" + RULE + "rules: []\n", "duplicate mapping key"),
        (doc(RULE + "    id: RULE_Z\n"), "duplicate mapping key"),
        (doc(RULE.replace("[x1]", "&p [x1]") + RULE.replace("RULE_A", "RULE_B").replace("[x1]", "*p")),
         "aliases are not allowed"),
    ],
)
def test_invalid_rules_are_rejected(tmp_path, registry, text, match):
    with pytest.raises(ConfigError, match=match):
        load_rule_set(write(tmp_path, text), registry)


def test_size_caps_are_enforced(tmp_path, registry, monkeypatch):
    monkeypatch.setattr(rules_module, "MAX_RULES", 1)
    with pytest.raises(ConfigError, match="too many rules"):
        load_rule_set(write(tmp_path, doc(RULE + RULE.replace("RULE_A", "RULE_B"))), registry)
    monkeypatch.setattr(rules_module, "MAX_PATTERNS_PER_RULE", 2)
    with pytest.raises(ConfigError, match="too many entries"):
        load_rule_set(write(tmp_path, doc(RULE.replace("[x1]", "[a1, b1, c1]"))), registry)


def test_error_messages_do_not_echo_patterns_or_paths(tmp_path, registry):
    secret = "SentinelPatternText"
    text = doc(RULE.replace("[x1]", f"[{secret}, 5]"))
    with pytest.raises(ConfigError) as ei:
        load_rule_set(write(tmp_path, text), registry)
    assert secret not in str(ei.value)
    assert str(tmp_path) not in str(ei.value)


def test_missing_rules_file(tmp_path, registry):
    with pytest.raises(ConfigError, match="not found"):
        load_rule_set(tmp_path / "nope.yaml", registry)


# ------------------------------------------------------------------ bundled config

OFFICIAL_CLASS_IDS = (
    "PERSONAL_IDENTITY",
    "CONTACT_INFORMATION",
    "GOVERNMENT_IDENTIFIERS",
    "FINANCIAL_INFORMATION",
    "LOCATION_ADDRESS",
)


def test_official_taxonomy_is_confirmed_and_has_the_five_official_ids():
    registry = load_class_registry(REPO_ROOT / "config" / "classes.yaml")
    assert registry.ids() == OFFICIAL_CLASS_IDS
    assert registry.taxonomy_status == "confirmed" and registry.is_provisional is False
    assert all(c.label and c.description for c in registry.classes)


def test_bundled_official_rules_load_against_the_confirmed_taxonomy():
    registry = load_class_registry(REPO_ROOT / "config" / "classes.yaml")
    rs = load_rule_set(REPO_ROOT / "config" / "rules.yaml", registry)
    # The taxonomy is confirmed; the heuristic rules stay provisional until deliberately frozen.
    assert rs.rules_status == "provisional" and rs.is_provisional is True
    assert {r.class_id for r in rs.rules} == set(registry.ids())  # every official class has a rule


def test_placeholder_ids_do_not_appear_in_the_official_config():
    for name in ("classes.yaml", "rules.yaml"):
        text = (REPO_ROOT / "config" / name).read_text(encoding="utf-8")
        assert "TEST_CLASS" not in text
