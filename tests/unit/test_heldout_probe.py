"""Structure and non-regression checks for the EXPLORATORY held-out probe.

The probe (tests/fixtures/heldout_probe/probe_columns.yaml) is NOT an independently validated
benchmark: it was written by the same engineer who wrote the rules, after the rules were frozen
and after that engineer had seen the synthetic benchmark. It is small and adversarial (many
look-alikes). These tests therefore pin only its structure and one weak property (the current
rules do not score below the baseline rules on it). They do not pin accuracy figures.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from fieldledger.classes import load_class_registry
from fieldledger.evaluation import load_labels
from fieldledger.rules import load_rule_set

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = REPO_ROOT / "tests" / "fixtures" / "heldout_probe" / "probe_columns.yaml"
NOT_PERSONAL = "NOT_PERSONAL_DATA"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "heldout_probe_script", REPO_ROOT / "scripts" / "heldout_probe.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["heldout_probe_script"] = module
    spec.loader.exec_module(module)
    return module


probe_script = _load_script()


@pytest.fixture(scope="module")
def registry():
    return load_class_registry(REPO_ROOT / "config" / "classes.yaml")


@pytest.fixture(scope="module")
def columns():
    return probe_script.load_probe(PROBE)


def test_probe_labels_are_official_classes_or_not_personal(columns, registry):
    allowed = set(registry.ids()) | {NOT_PERSONAL}
    assert {c["label"] for c in columns} <= allowed
    assert all(set(c) == {"name", "label", "kind"} for c in columns)


def test_probe_covers_every_class_and_both_positive_and_negative_names(columns, registry):
    labels = [c["label"] for c in columns]
    for class_id in registry.ids():
        assert labels.count(class_id) >= 8, class_id
    assert labels.count(NOT_PERSONAL) >= 40


def test_probe_names_are_unique_and_disjoint_from_the_synthetic_benchmark(columns, registry):
    """The held-out property: no probe name appears in the benchmark labels."""
    names = [c["name"] for c in columns]
    assert len(set(names)) == len(names)
    labels = load_labels(REPO_ROOT / "data" / "synthetic" / "ground_truth" / "labels.yaml", registry)
    bench = {c for s in labels.sources for (_, c, _) in s.positives}
    bench |= {c for s in labels.sources for (_, c) in (s.not_personal_data or ())}
    assert not set(n.casefold() for n in names) & set(b.casefold() for b in bench)


def test_probe_file_declares_it_is_exploratory():
    text = PROBE.read_text(encoding="utf-8")
    assert "EXPLORATORY" in text and "NOT an independently validated benchmark" in text
    assert "label_origin: author_judgement_exploratory" in text


def _micro_f1(result):
    per = result["per_class"].values()
    tp, fp, fn = (sum(v[k] for v in per) for k in ("tp", "fp", "fn"))
    p, r = tp / (tp + fp), tp / (tp + fn)
    return tp, 2 * p * r / (p + r)


def test_current_rules_do_not_score_below_the_baseline_rules_on_the_probe(columns, registry):
    ids = tuple(registry.ids())
    current = load_rule_set(REPO_ROOT / "config" / "rules.yaml", registry)
    baseline = load_rule_set(REPO_ROOT / "tests" / "fixtures" / "rules_baseline_d3ab9c2.yaml", registry)
    tp_now, f1_now = _micro_f1(probe_script.score(current, columns, ids))
    tp_old, f1_old = _micro_f1(probe_script.score(baseline, columns, ids))
    assert tp_now >= tp_old
    assert f1_now >= f1_old
