import dataclasses
import json
import logging

import pytest

from fieldledger import evaluation as evaluation_module
from fieldledger.classes import load_class_registry
from fieldledger.detector import detect_candidates
from fieldledger.errors import ConfigError
from fieldledger.evaluation import (
    LABEL_ORIGINS,
    SCOPE_NOTICES,
    evaluate,
    load_labels,
)
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
required_class_count: 3
classes:
  - {id: ALPHA_1, label: Alpha}
  - {id: BETA_2, label: Beta}
  - {id: GAMMA_3, label: Gamma}
"""

RULES_TEXT = """\
rules_status: provisional
rules:
  - {id: R_A, class_id: ALPHA_1, match: token, patterns: [alp]}
  - {id: R_B, class_id: BETA_2, match: token, patterns: [bet]}
  - {id: R_G, class_id: GAMMA_3, match: token, patterns: [gam]}
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


def make_profile(tables, source_name="s.csv"):
    built = []
    for table_name, columns in tables.items():
        cols = tuple(
            ColumnProfile(c, i, BroadType.STRING, 1, 0) for i, c in enumerate(columns, start=1)
        )
        built.append(TableProfile(table_name, cols, row_count=1))
    return SourceProfile(SourceType.CSV, source_name, TypeBasis.OBSERVED_VALUES, tuple(built))


def labels_text(sources, origin="team_authored_synthetic"):
    lines = [f"label_origin: {origin}", "sources:"]
    for key, positives in sources:
        lines.append(f"  - source_key: {key}")
        if not positives:
            lines.append("    positives: []")
            continue
        lines.append("    positives:")
        for table, column, class_id in positives:
            lines.append(f"      - {{table: {table}, column: {column}, class_id: {class_id}}}")
    return "\n".join(lines) + "\n"


def run(tmp_path, registry, ruleset, profiles, sources, extra_findings=()):
    """profiles: {source_key: SourceProfile}; sources: labels spec for labels_text."""
    labels = load_labels(write(tmp_path, labels_text(sources), "labels.yaml"), registry)
    findings = []
    for key, profile in profiles.items():
        findings.extend(detect_candidates(profile, ruleset, source_key=key))
    findings.extend(extra_findings)
    return evaluate(findings, labels, profiles, registry=registry, ruleset=ruleset)


def by_class(report):
    return {m.class_id: m for m in report.per_class}


# ------------------------------------------------------------------ load_labels


def test_valid_labels_load(tmp_path, registry):
    text = labels_text(
        [("a.csv", [("t", "alp_1", "ALPHA_1"), ("t", "bet_1", "BETA_2")]), ("b.csv", [])]
    )
    ls = load_labels(write(tmp_path, text, "l.yaml"), registry)
    assert ls.label_origin == "team_authored_synthetic"
    assert [s.source_key for s in ls.sources] == ["a.csv", "b.csv"]
    assert ls.sources[0].positives == (("t", "alp_1", "ALPHA_1"), ("t", "bet_1", "BETA_2"))
    assert ls.sources[1].positives == ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ls.label_origin = "organizer_provided"  # type: ignore[misc]


GOOD_SRC = "  - source_key: a.csv\n    positives:\n      - {table: t, column: c, class_id: ALPHA_1}\n"


@pytest.mark.parametrize(
    "text, match",
    [
        ("sources:\n" + GOOD_SRC, "label_origin"),
        ("label_origin: guessed\nsources:\n" + GOOD_SRC, "label_origin"),
        ("label_origin: team_authored_synthetic\n", "non-empty"),
        ("label_origin: team_authored_synthetic\nsources: []\n", "non-empty"),
        ("label_origin: team_authored_synthetic\nsources:\n" + GOOD_SRC + "extra: 1\n", "unknown top-level"),
        ("label_origin: team_authored_synthetic\nsources:\n  - text\n", "must be a mapping"),
        ("label_origin: team_authored_synthetic\nsources:\n" + GOOD_SRC + "    note: x\n", "unknown keys"),
        ("label_origin: team_authored_synthetic\nsources:\n  - source_key: a.csv\n", "missing keys"),
        ("label_origin: team_authored_synthetic\nsources:\n" + GOOD_SRC.replace("a.csv", "../a.csv"), "source_key is invalid"),
        ("label_origin: team_authored_synthetic\nsources:\n" + GOOD_SRC + GOOD_SRC, "duplicate source_key"),
        ("label_origin: team_authored_synthetic\nsources:\n" + GOOD_SRC.replace("positives:\n      - {table: t, column: c, class_id: ALPHA_1}\n", "positives: nope\n"), "positives must be a list"),
        ("label_origin: team_authored_synthetic\nsources:\n" + GOOD_SRC.replace("ALPHA_1", "NOPE_9"), "class_id is not in the class registry"),
        ("label_origin: team_authored_synthetic\nsources:\n" + GOOD_SRC.replace("ALPHA_1", "alpha_1"), "class_id is not in the class registry"),
        ("label_origin: team_authored_synthetic\nsources:\n" + GOOD_SRC.replace("table: t, ", ""), "missing keys"),
        ("label_origin: team_authored_synthetic\nsources:\n" + GOOD_SRC.replace("table: t", "table: ''"), r"\.table"),
        ("label_origin: team_authored_synthetic\nsources:\n" + GOOD_SRC.replace("column: c", "column: 5"), r"\.column"),
        ("label_origin: team_authored_synthetic\nsources:\n" + GOOD_SRC.replace("class_id: ALPHA_1}", "class_id: ALPHA_1, x: 1}"), "unknown keys"),
        ("label_origin: team_authored_synthetic\nsources:\n" + GOOD_SRC + "      - {table: t, column: c, class_id: ALPHA_1}\n", "duplicate label"),
        ("label_origin: team_authored_synthetic\nlabel_origin: organizer_provided\nsources:\n" + GOOD_SRC, "duplicate mapping key"),
    ],
)
def test_invalid_labels_are_rejected(tmp_path, registry, text, match):
    with pytest.raises(ConfigError, match=match):
        load_labels(write(tmp_path, text, "l.yaml"), registry)


def test_label_caps_are_enforced(tmp_path, registry, monkeypatch):
    monkeypatch.setattr(evaluation_module, "MAX_LABEL_SOURCES", 1)
    text = labels_text([("a.csv", []), ("b.csv", [])])
    with pytest.raises(ConfigError, match="too many labelled sources"):
        load_labels(write(tmp_path, text, "l.yaml"), registry)
    monkeypatch.setattr(evaluation_module, "MAX_LABELS_PER_SOURCE", 1)
    text = labels_text([("a.csv", [("t", "c1", "ALPHA_1"), ("t", "c2", "ALPHA_1")])])
    with pytest.raises(ConfigError, match="too many entries"):
        load_labels(write(tmp_path, text, "l2.yaml"), registry)


def test_label_errors_do_not_echo_names_or_paths(tmp_path, registry):
    text = labels_text([("sentinel/dir.csv", [("SentinelTable", "SentinelColumn", "NOPE_9")])])
    with pytest.raises(ConfigError) as ei:
        load_labels(write(tmp_path, text, "l.yaml"), registry)
    msg = str(ei.value)
    assert not any(s in msg for s in ("sentinel", "SentinelTable", "SentinelColumn", str(tmp_path)))


def test_both_label_origins_have_a_scope_notice(tmp_path, registry):
    assert set(LABEL_ORIGINS) == set(SCOPE_NOTICES)
    for origin in LABEL_ORIGINS:
        text = labels_text([("a.csv", [])], origin=origin)
        assert load_labels(write(tmp_path, text, f"{origin}.yaml"), registry).label_origin == origin
    assert "team-authored synthetic" in SCOPE_NOTICES["team_authored_synthetic"]
    assert all("not a measure of general accuracy" in n for n in SCOPE_NOTICES.values())


# ------------------------------------------------------------------ evaluate: metrics


def test_hand_computed_metrics_with_undefined_values(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["id", "alp_a", "alp_b", "bet_x", "gam_y", "opaque"]})
    report = run(
        tmp_path, registry, ruleset,
        {"s.csv": profile},
        [("s.csv", [
            ("t", "alp_a", "ALPHA_1"),     # TP alpha
            ("t", "opaque", "ALPHA_1"),    # FN alpha (no rule matches the name)
            ("t", "bet_x", "BETA_2"),      # TP beta
        ])],
    )
    m = by_class(report)
    # alpha: predicted {alp_a, alp_b}, labelled {alp_a, opaque} -> TP1 FP1 FN1
    assert (m["ALPHA_1"].true_positives, m["ALPHA_1"].false_positives, m["ALPHA_1"].false_negatives) == (1, 1, 1)
    assert m["ALPHA_1"].precision == 0.5 and m["ALPHA_1"].recall == 0.5
    # beta: perfect
    assert (m["BETA_2"].precision, m["BETA_2"].recall) == (1.0, 1.0)
    # gamma: one prediction, no labelled positives -> precision 0.0, recall undefined
    assert (m["GAMMA_3"].true_positives, m["GAMMA_3"].false_positives, m["GAMMA_3"].false_negatives) == (0, 1, 0)
    assert m["GAMMA_3"].precision == 0.0 and m["GAMMA_3"].recall is None
    assert report.labelled_source_count == 1 and report.labelled_column_count == 6
    assert report.unscored_finding_count == 0


def test_precision_is_none_when_a_class_has_labels_but_no_predictions(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["opaque", "id"]})
    report = run(tmp_path, registry, ruleset, {"s.csv": profile}, [("s.csv", [("t", "opaque", "BETA_2")])])
    beta = by_class(report)["BETA_2"]
    assert (beta.true_positives, beta.false_positives, beta.false_negatives) == (0, 0, 1)
    assert beta.precision is None and beta.recall == 0.0


def test_classes_with_no_labels_and_no_predictions_are_fully_undefined(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["id"]})
    report = run(tmp_path, registry, ruleset, {"s.csv": profile}, [("s.csv", [])])
    for m in report.per_class:
        assert (m.true_positives, m.false_positives, m.false_negatives) == (0, 0, 0)
        assert m.precision is None and m.recall is None


def test_per_class_results_follow_registry_order_and_are_independent(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["alp_bet"]})  # ambiguous: ALPHA_1 and BETA_2
    report = run(tmp_path, registry, ruleset, {"s.csv": profile}, [("s.csv", [("t", "alp_bet", "ALPHA_1")])])
    assert [m.class_id for m in report.per_class] == list(registry.ids())
    m = by_class(report)
    assert (m["ALPHA_1"].true_positives, m["ALPHA_1"].false_positives) == (1, 0)
    assert (m["BETA_2"].true_positives, m["BETA_2"].false_positives) == (0, 1)
    (amb,) = report.ambiguous_columns
    assert amb.class_ids == ("ALPHA_1", "BETA_2")


def test_no_aggregate_or_f_score_is_reported(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["alp_a"]})
    d = run(tmp_path, registry, ruleset, {"s.csv": profile}, [("s.csv", [("t", "alp_a", "ALPHA_1")])]).to_dict()
    flat = json.dumps(d).lower()
    for word in ("f1", "f_score", "f-score", "accuracy_score", "macro", "micro", "average"):
        assert word not in flat


def test_same_basename_sources_are_scored_separately(tmp_path, registry, ruleset):
    p = make_profile({"people": ["alp_ref"]}, source_name="people.csv")
    report = run(
        tmp_path, registry, ruleset,
        {"a/people.csv": p, "b/people.csv": p},
        [("a/people.csv", [("people", "alp_ref", "ALPHA_1")]), ("b/people.csv", [])],
    )
    alpha = by_class(report)["ALPHA_1"]
    assert (alpha.true_positives, alpha.false_positives, alpha.false_negatives) == (1, 1, 0)


def test_findings_in_unlabelled_sources_are_counted_not_scored(tmp_path, registry, ruleset):
    labelled = make_profile({"t": ["alp_a"]})
    other = make_profile({"t": ["alp_b", "bet_b"]}, source_name="other.csv")
    labels = load_labels(
        write(tmp_path, labels_text([("s.csv", [("t", "alp_a", "ALPHA_1")])]), "l.yaml"), registry
    )
    findings = detect_candidates(labelled, ruleset, source_key="s.csv") + detect_candidates(
        other, ruleset, source_key="other.csv"
    )
    report = evaluate(findings, labels, {"s.csv": labelled}, registry=registry, ruleset=ruleset)
    assert report.unscored_finding_count == 2
    assert by_class(report)["ALPHA_1"].false_positives == 0
    assert by_class(report)["BETA_2"].false_positives == 0


# ------------------------------------------------------------------ evaluate: identity and errors


def test_identity_is_exact_and_case_sensitive(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["Alp_Col"]})
    with pytest.raises(ConfigError, match="not in the profiled source"):
        run(tmp_path, registry, ruleset, {"s.csv": profile}, [("s.csv", [("t", "alp_col", "ALPHA_1")])])
    with pytest.raises(ConfigError, match="not in the profiled source"):
        run(tmp_path, registry, ruleset, {"s.csv": profile}, [("s.csv", [("T", "Alp_Col", "ALPHA_1")])])


def test_labelled_source_missing_from_profiles_is_an_error(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["alp_a"]})
    with pytest.raises(ConfigError, match="not among the profiled sources"):
        run(tmp_path, registry, ruleset, {"s.csv": profile}, [("other.csv", [])])


def test_evaluation_errors_do_not_echo_names(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["alp_a"]})
    with pytest.raises(ConfigError) as ei:
        run(tmp_path, registry, ruleset, {"s.csv": profile}, [("s.csv", [("SentinelTable", "SentinelColumn", "ALPHA_1")])])
    assert "Sentinel" not in str(ei.value)


def test_duplicate_finding_identities_are_rejected(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["alp_a"]})
    labels = load_labels(write(tmp_path, labels_text([("s.csv", [])]), "l.yaml"), registry)
    findings = detect_candidates(profile, ruleset, source_key="s.csv")
    with pytest.raises(ValueError, match="duplicate finding identity"):
        evaluate(findings + findings, labels, {"s.csv": profile}, registry=registry, ruleset=ruleset)


def test_finding_with_unknown_class_or_column_is_rejected(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["alp_a"]})
    labels = load_labels(write(tmp_path, labels_text([("s.csv", [])]), "l.yaml"), registry)
    stray_class = Finding("s.csv", "t", "alp_a", 1, "NOPE_9", ("R_A",))
    stray_column = Finding("s.csv", "t", "other", 1, "ALPHA_1", ("R_A",))
    for bad in (stray_class, stray_column):
        with pytest.raises(ValueError):
            evaluate([bad], labels, {"s.csv": profile}, registry=registry, ruleset=ruleset)
    with pytest.raises(TypeError):
        evaluate([object()], labels, {"s.csv": profile}, registry=registry, ruleset=ruleset)  # type: ignore[list-item]


# ------------------------------------------------------------------ report content


def test_report_carries_provenance_and_stays_provisional(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["alp_a"]})
    report = run(tmp_path, registry, ruleset, {"s.csv": profile}, [("s.csv", [])])
    assert registry.is_provisional and ruleset.is_provisional
    assert report.taxonomy_status == "provisional"
    assert report.rules_status == "provisional"
    assert report.label_origin == "team_authored_synthetic"
    assert report.scope_notice == SCOPE_NOTICES["team_authored_synthetic"]


def test_report_to_dict_is_json_serialisable_with_null_for_undefined(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["alp_a", "gam_z"]})
    report = run(tmp_path, registry, ruleset, {"s.csv": profile}, [("s.csv", [("t", "alp_a", "ALPHA_1")])])
    d = report.to_dict()
    assert json.loads(json.dumps(d)) == d
    assert list(d) == [
        "scope_notice", "label_origin", "taxonomy_status", "rules_status",
        "labelled_source_count", "labelled_column_count", "unscored_finding_count",
        "ambiguous_columns", "classes",
    ]
    classes = {c["class_id"]: c for c in d["classes"]}
    assert classes["BETA_2"]["precision"] is None and classes["BETA_2"]["recall"] is None
    assert classes["GAMMA_3"]["recall"] is None and classes["GAMMA_3"]["precision"] == 0.0


def test_report_has_no_legal_or_compliance_fields(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["alp_a"]})
    d = run(tmp_path, registry, ruleset, {"s.csv": profile}, [("s.csv", [])]).to_dict()
    flat_keys = json.dumps(list(d) + [k for c in d["classes"] for k in c]).lower()
    for word in ("lawful", "legal", "complian", "consent", "violation", "verdict"):
        assert word not in flat_keys


def test_evaluation_logs_counts_only(tmp_path, registry, ruleset, caplog):
    profile = make_profile({"secret_table": ["alp_secret_column"]})
    with caplog.at_level(logging.DEBUG, logger="fieldledger"):
        run(tmp_path, registry, ruleset, {"secret/dir.csv": profile},
            [("secret/dir.csv", [("secret_table", "alp_secret_column", "ALPHA_1")])])
    assert "secret" not in caplog.text
    assert "evaluation: classes=3" in caplog.text


# ------------------------------------------------------------------ explicit negatives


def neg_labels_text(positives, negatives, key="s.csv"):
    lines = ["label_origin: team_authored_synthetic", "sources:", f"  - source_key: {key}"]
    lines.append("    positives:" if positives else "    positives: []")
    for table, column, class_id in positives:
        lines.append(f"      - {{table: {table}, column: {column}, class_id: {class_id}}}")
    lines.append("    not_personal_data:" if negatives else "    not_personal_data: []")
    for table, column in negatives:
        lines.append(f"      - {{table: {table}, column: {column}}}")
    return "\n".join(lines) + "\n"


def test_explicit_negatives_load_and_complete_labelling_is_scored(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["alp_a", "plain"]})
    text = neg_labels_text([("t", "alp_a", "ALPHA_1")], [("t", "plain")])
    labels = load_labels(write(tmp_path, text, "l.yaml"), registry)
    assert labels.sources[0].not_personal_data == (("t", "plain"),)
    findings = detect_candidates(profile, ruleset, source_key="s.csv")
    report = evaluate(findings, labels, {"s.csv": profile}, registry=registry, ruleset=ruleset)
    alpha = by_class(report)["ALPHA_1"]
    assert (alpha.true_positives, alpha.false_positives, alpha.false_negatives) == (1, 0, 0)


def test_sources_without_the_negatives_key_keep_implicit_negatives(tmp_path, registry):
    labels = load_labels(write(tmp_path, labels_text([("s.csv", [("t", "c", "ALPHA_1")])]), "l.yaml"), registry)
    assert labels.sources[0].not_personal_data is None


def test_incomplete_explicit_labelling_is_an_error_not_an_implicit_negative(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["alp_a", "plain", "forgotten"]})
    text = neg_labels_text([("t", "alp_a", "ALPHA_1")], [("t", "plain")])
    labels = load_labels(write(tmp_path, text, "l.yaml"), registry)
    findings = detect_candidates(profile, ruleset, source_key="s.csv")
    with pytest.raises(ConfigError, match=r"leaves 1 profiled column\(s\) unlabelled") as ei:
        evaluate(findings, labels, {"s.csv": profile}, registry=registry, ruleset=ruleset)
    assert "forgotten" not in str(ei.value)


def test_explicit_negative_must_exist_in_the_profile(tmp_path, registry, ruleset):
    profile = make_profile({"t": ["alp_a"]})
    text = neg_labels_text([("t", "alp_a", "ALPHA_1")], [("t", "ghost")])
    labels = load_labels(write(tmp_path, text, "l.yaml"), registry)
    with pytest.raises(ConfigError, match=r"not_personal_data\[0\] refers to a column") as ei:
        evaluate([], labels, {"s.csv": profile}, registry=registry, ruleset=ruleset)
    assert "ghost" not in str(ei.value)


@pytest.mark.parametrize(
    "negatives, match",
    [
        ("      - {table: t, column: c}\n      - {table: t, column: c}\n", "duplicate label"),
        ("      - {table: t, column: c, class_id: ALPHA_1}\n", "unknown keys"),
        ("      - {table: t}\n", "missing keys"),
        ("      - {table: '', column: c}\n", r"\.table"),
        ("      - {table: t, column: 5}\n", r"\.column"),
        ("      - text\n", "must be a mapping"),
        ("      - {table: t, column: shared}\n", "both personal and not personal"),
    ],
)
def test_invalid_explicit_negatives_are_rejected(tmp_path, registry, negatives, match):
    text = (
        "label_origin: team_authored_synthetic\nsources:\n  - source_key: s.csv\n"
        "    positives:\n      - {table: t, column: shared, class_id: ALPHA_1}\n"
        "    not_personal_data:\n" + negatives
    )
    with pytest.raises(ConfigError, match=match):
        load_labels(write(tmp_path, text, "l.yaml"), registry)


def test_not_personal_data_must_be_a_list(tmp_path, registry):
    text = (
        "label_origin: team_authored_synthetic\nsources:\n  - source_key: s.csv\n"
        "    positives: []\n    not_personal_data: nope\n"
    )
    with pytest.raises(ConfigError, match="not_personal_data must be a list"):
        load_labels(write(tmp_path, text, "l.yaml"), registry)
