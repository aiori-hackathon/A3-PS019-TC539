"""Discovery -> profiling -> detection -> evaluation on the generated synthetic dataset.

The dataset is regenerated into tmp_path by scripts/generate_synthetic_dataset.py (fixed
seed) and covers CSV and SQLite sources, the official five classes, positives, negatives and
look-alikes. Results below describe THIS team-authored synthetic set and the provisional
name-only rules; they are not general accuracy and not a legal assessment.
"""

import hashlib
import importlib.util
import json
import logging
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from fieldledger.classes import load_class_registry
from fieldledger.detector import detect_candidates
from fieldledger.discovery import discover_sources
from fieldledger.evaluation import evaluate, load_labels
from fieldledger.findings import source_key_for
from fieldledger.profiler import profile_source
from fieldledger.profiles import SourceType
from fieldledger.rules import load_rule_set

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_synthetic_dataset", REPO_ROOT / "scripts" / "generate_synthetic_dataset.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_synthetic_dataset"] = module
    spec.loader.exec_module(module)
    return module


gen = _load_generator()

EXPECTED_SOURCES = [
    "app.db",
    "crm/archive/customers.csv",
    "crm/customers.csv",
    "finance/payments.csv",
    "hr/employees.csv",
    "ops/orders.csv",
]

# (TP, FP, FN) per official class for seed 20261027 and config/rules.yaml. A change to the
# rules, the pools or the seed changes these numbers on purpose: update them deliberately.
EXPECTED_COUNTS = {
    "PERSONAL_IDENTITY": (24, 7, 5),
    "CONTACT_INFORMATION": (22, 10, 5),
    "GOVERNMENT_IDENTIFIERS": (21, 3, 5),
    "FINANCIAL_INFORMATION": (21, 4, 5),
    "LOCATION_ADDRESS": (26, 11, 5),
}


@pytest.fixture
def workspace(tmp_path):
    base = tmp_path / "input"
    gen.generate_dataset(base)
    return base


@pytest.fixture
def registry():
    return load_class_registry(REPO_ROOT / "config" / "classes.yaml")


@pytest.fixture
def ruleset(registry):
    return load_rule_set(REPO_ROOT / "config" / "rules.yaml", registry)


def snapshot(directory):
    return {
        p.relative_to(directory).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def scan(base, ruleset):
    profiles, findings = {}, []
    for path in discover_sources(base):
        key = source_key_for(path, base)
        profile = profile_source(path, base)
        profiles[key] = profile
        findings.extend(detect_candidates(profile, ruleset, source_key=key))
    return profiles, findings


def full_run(base, registry, ruleset):
    profiles, findings = scan(base, ruleset)
    labels = load_labels(base / "ground_truth" / "labels.yaml", registry)
    return profiles, findings, evaluate(findings, labels, profiles, registry=registry, ruleset=ruleset)


def test_discovery_finds_every_data_source_and_ignores_non_data_files(workspace):
    keys = [source_key_for(p, workspace) for p in discover_sources(workspace)]
    assert keys == EXPECTED_SOURCES  # labels, metadata, manifest and docs are not sources
    assert len(set(keys)) == len(keys)  # crm/customers.csv and crm/archive/customers.csv stay distinct


def test_csv_and_sqlite_sources_are_both_profiled(workspace, ruleset):
    profiles, _ = scan(workspace, ruleset)
    kinds = {k: p.source_type for k, p in profiles.items()}
    assert kinds["app.db"] is SourceType.SQLITE
    assert all(v is SourceType.CSV for k, v in kinds.items() if k != "app.db")
    assert len(profiles["app.db"].tables) == 5
    assert sum(len(t.columns) for p in profiles.values() for t in p.tables) == 272


def test_per_class_counts_match_the_recorded_snapshot(workspace, registry, ruleset):
    _, _, report = full_run(workspace, registry, ruleset)
    got = {m.class_id: (m.true_positives, m.false_positives, m.false_negatives) for m in report.per_class}
    assert got == EXPECTED_COUNTS
    assert [m.class_id for m in report.per_class] == list(registry.ids())


def test_metrics_follow_from_the_counts_and_are_defined_for_every_official_class(workspace, registry, ruleset):
    _, _, report = full_run(workspace, registry, ruleset)
    for m in report.per_class:
        tp, fp, fn = m.true_positives, m.false_positives, m.false_negatives
        assert m.precision is not None and m.recall is not None  # each class has labels and predictions
        assert m.precision == pytest.approx(tp / (tp + fp))
        assert m.recall == pytest.approx(tp / (tp + fn))
        assert tp + fn == gen_positives(workspace, m.class_id)


def gen_positives(base, class_id):
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    return manifest["positives_by_class"][class_id]


def test_report_is_confirmed_taxonomy_provisional_rules_and_scoped(workspace, registry, ruleset):
    _, _, report = full_run(workspace, registry, ruleset)
    assert report.taxonomy_status == "confirmed"
    assert report.rules_status == "provisional"
    assert report.label_origin == "team_authored_synthetic"
    assert "team-authored synthetic" in report.scope_notice and "not a measure of general accuracy" in report.scope_notice
    assert report.labelled_source_count == 6 and report.labelled_column_count == 272
    assert report.unscored_finding_count == 0


def test_ambiguous_multi_class_columns_are_reported_not_resolved(workspace, registry, ruleset):
    _, findings, report = full_run(workspace, registry, ruleset)
    got = {(a.column_name, a.class_ids) for a in report.ambiguous_columns}
    assert got == {
        ("email_address", ("CONTACT_INFORMATION", "LOCATION_ADDRESS")),
        ("pan_card_number", ("FINANCIAL_INFORMATION", "GOVERNMENT_IDENTIFIERS")),
        ("voter_card_no", ("FINANCIAL_INFORMATION", "GOVERNMENT_IDENTIFIERS")),
    }
    # one finding per class for an ambiguous column
    email = [f for f in findings if f.column_name == "email_address"]
    assert sorted(f.class_id for f in email) == ["CONTACT_INFORMATION", "LOCATION_ADDRESS"]


def test_opaque_and_lookalike_columns_behave_as_documented(workspace, registry, ruleset):
    _, findings, _ = full_run(workspace, registry, ruleset)
    flagged = {f.column_name for f in findings}
    for opaque in ("ph_no", "uid_no", "fname", "addr_ln1", "acc_num"):  # real data, missed by name
        assert opaque not in flagged
    for lookalike in ("ip_address", "phone_model", "email_opt_in", "account_age_days"):
        assert lookalike in flagged  # keyword look-alikes are candidates (labelled not personal)
    for value_lookalike in ("order_ref", "tracking_number", "invoice_code", "batch_serial"):
        assert value_lookalike not in flagged  # innocuous names, personal-looking values


def test_no_cell_values_names_or_absolute_paths_in_logs_or_outputs(workspace, registry, ruleset, caplog):
    with caplog.at_level(logging.DEBUG, logger="fieldledger"):
        profiles, findings, report = full_run(workspace, registry, ruleset)
    outputs = json.dumps(
        {
            "findings": [f.to_dict() for f in findings],
            "report": report.to_dict(),
            "profiles": [p.to_dict() for p in profiles.values()],
        }
    )
    values = set()
    for p in workspace.rglob("*.csv"):
        for line in p.read_text(encoding="utf-8").splitlines()[1:]:
            values.update(v for v in line.split(",") if len(v) >= 10)
    with closing(sqlite3.connect(workspace / "app.db")) as conn:
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for row in conn.execute(f'SELECT * FROM "{table}"'):
                values.update(v for v in map(str, row) if len(v) >= 10)
    assert len(values) > 100
    for v in values:
        assert v not in outputs and v not in caplog.text
    assert str(workspace) not in outputs and str(workspace) not in caplog.text
    for name in ("full_name", "aadhaar_number", "customers", "members"):
        assert name not in caplog.text  # identifiers stay out of logs


def test_input_files_are_not_modified(workspace, registry, ruleset):
    before = snapshot(workspace)
    full_run(workspace, registry, ruleset)
    assert snapshot(workspace) == before


def test_results_are_deterministic_across_runs(workspace, registry, ruleset):
    runs = []
    for _ in range(2):
        _, findings, report = full_run(workspace, registry, ruleset)
        runs.append((json.dumps([f.to_dict() for f in findings]), json.dumps(report.to_dict())))
    assert runs[0] == runs[1]


def test_committed_dataset_gives_the_same_report_as_the_regenerated_one(workspace, registry, ruleset):
    committed = REPO_ROOT / "data" / "synthetic"
    assert committed.is_dir()
    _, _, fresh = full_run(workspace, registry, ruleset)
    _, _, stored = full_run(committed, registry, ruleset)
    assert json.dumps(fresh.to_dict()) == json.dumps(stored.to_dict())
