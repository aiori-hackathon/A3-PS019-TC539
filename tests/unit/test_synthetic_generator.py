"""The deterministic synthetic-data generator and the committed dataset it produced."""

import csv
import importlib.util
import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from fieldledger.classes import load_class_registry
from fieldledger.evaluation import load_labels
from fieldledger.strict_yaml import load_strict_yaml_mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "generate_synthetic_dataset.py"
COMMITTED = REPO_ROOT / "data" / "synthetic"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_synthetic_dataset", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_synthetic_dataset"] = module  # needed before exec for some stdlib users
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    manifest = gen.generate_dataset(out)
    return out, manifest


@pytest.fixture(scope="module")
def registry():
    return load_class_registry(REPO_ROOT / "config" / "classes.yaml")


def sqlite_dump(path):
    with closing(sqlite3.connect(path)) as conn:
        return list(conn.iterdump())


def norm(path):
    return path.read_bytes().replace(b"\r\n", b"\n")  # tolerate git autocrlf checkouts


def files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


# ------------------------------------------------------------------ determinism


def test_same_seed_gives_identical_output(dataset, tmp_path):
    out, _ = dataset
    other = tmp_path / "again"
    gen.generate_dataset(other)
    assert files(out) == files(other)
    for rel in files(out):
        if rel == "app.db":
            assert sqlite_dump(out / rel) == sqlite_dump(other / rel)
        else:
            assert norm(out / rel) == norm(other / rel), rel


def test_different_seed_changes_the_data_but_not_the_labelled_structure(dataset, tmp_path):
    out, manifest = dataset
    other = tmp_path / "other"
    other_manifest = gen.generate_dataset(other, seed=gen.DEFAULT_SEED + 1)
    assert norm(out / "crm" / "customers.csv") != norm(other / "crm" / "customers.csv")
    assert other_manifest["positives_by_class"] == manifest["positives_by_class"]


def test_committed_dataset_matches_the_generator(dataset):
    out, _ = dataset
    assert COMMITTED.is_dir(), "run: python scripts/generate_synthetic_dataset.py --out data/synthetic"
    assert files(COMMITTED) == files(out)
    for rel in files(out):
        if rel == "app.db":
            assert sqlite_dump(COMMITTED / rel) == sqlite_dump(out / rel)
        else:
            assert norm(COMMITTED / rel) == norm(out / rel), rel


def test_cli_refuses_a_non_empty_directory_without_overwrite(tmp_path, capsys):
    target = tmp_path / "o"
    assert gen.main(["--out", str(target)]) == 0
    assert gen.main(["--out", str(target)]) == 2
    assert "not empty" in capsys.readouterr().err
    assert gen.main(["--out", str(target), "--overwrite"]) == 0


# ------------------------------------------------------------------ size and labelling


def test_class_ids_match_the_official_registry(registry):
    assert gen.CLASS_IDS == registry.ids()


def test_dataset_meets_the_recommended_scale(dataset):
    _, m = dataset
    for class_id in gen.CLASS_IDS:
        assert m["positives_by_class"][class_id] >= 20, class_id
    ratio = m["negatives_total"] / m["positives_total"]
    assert 0.8 <= ratio <= 1.25  # a comparable number of negatives
    assert m["columns_total"] == m["positives_total"] + m["negatives_total"]
    assert m["columns_by_group"]["negative_value_lookalike"] > 0
    assert m["columns_by_group"]["negative_name_lookalike"] > 0
    assert m["columns_by_group"]["positive_opaque_name"] > 0


def test_column_names_are_unique_across_all_pools():
    names = [s[0] for s in gen._specs()]
    assert len(names) == len(set(names))


def read_columns(root):
    """Independent of the profilers: columns straight from the files."""
    cols = {}
    for rel in files(root):
        if rel.endswith(".csv"):
            with open(root / rel, encoding="utf-8", newline="") as fh:
                header = next(csv.reader(fh))
            cols[rel] = {(Path(rel).stem, c) for c in header}
        elif rel == "app.db":
            with closing(sqlite3.connect(root / rel)) as conn:
                found = set()
                for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                    found |= {(table, r[1]) for r in conn.execute(f'PRAGMA table_info("{table}")')}
            cols[rel] = found
    return cols


def test_every_column_is_labelled_exactly_once(dataset, registry):
    out, m = dataset
    labels = load_labels(out / "ground_truth" / "labels.yaml", registry)
    actual = read_columns(out)
    assert {s.source_key for s in labels.sources} == set(actual)
    pos_total = neg_total = 0
    for s in labels.sources:
        assert s.not_personal_data is not None
        pos = {(t, c) for t, c, _ in s.positives}
        neg = set(s.not_personal_data)
        assert not pos & neg
        assert pos | neg == actual[s.source_key]
        pos_total += len(pos)
        neg_total += len(neg)
    assert (pos_total, neg_total) == (m["positives_total"], m["negatives_total"])
    per_class = {c: 0 for c in gen.CLASS_IDS}
    for s in labels.sources:
        for _t, _c, class_id in s.positives:
            per_class[class_id] += 1
    assert per_class == m["positives_by_class"]


def test_same_basename_files_exist_in_different_directories(dataset):
    out, _ = dataset
    assert (out / "crm" / "customers.csv").is_file() and (out / "crm" / "archive" / "customers.csv").is_file()


def test_csv_files_are_utf8_with_header_and_consistent_rows(dataset):
    out, m = dataset
    for rel in files(out):
        if not rel.endswith(".csv"):
            continue
        raw = (out / rel).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8")  # strict
        rows = list(csv.reader(text.splitlines()))
        header, body = rows[0], rows[1:]
        assert len(header) == len(set(header)) > 0
        assert len(body) == m["rows_per_table"]
        assert all(len(r) == len(header) for r in body)


def test_sqlite_dataset_has_five_tables_of_the_expected_size(dataset):
    out, m = dataset
    with closing(sqlite3.connect(out / "app.db")) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        assert tables == ["branches", "inventory", "kyc_records", "members", "web_events"]
        for t in tables:
            assert conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] == m["rows_per_table"]


# ------------------------------------------------------------------ synthetic-value safety


def all_cells(root):
    for rel in files(root):
        if rel.endswith(".csv"):
            with open(root / rel, encoding="utf-8", newline="") as fh:
                for row in list(csv.reader(fh))[1:]:
                    yield from row
    with closing(sqlite3.connect(root / "app.db")) as conn:
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            for row in conn.execute(f'SELECT * FROM "{table}"'):
                yield from (str(v) for v in row)


REAL_LOOKING = {
    "email with a routable domain": re.compile(r"@(?!example\.invalid$|synthbank$)[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"),
    "valid-looking Indian mobile": re.compile(r"^(?:\+91)?[6-9]\d{9}$"),
    "valid-looking Aadhaar": re.compile(r"^[2-9]\d{11}$"),
    "PAN with an assigned holder type": re.compile(r"^[A-Z]{3}[ABCFGHLJPTK][A-Z]\d{4}[A-Z]$"),
    "valid-looking PIN code": re.compile(r"^[1-9]\d{5}$"),
    "card with a real-looking IIN": re.compile(r"^[1-9]\d{15}$"),
}


def test_values_are_invalid_by_construction_where_a_rule_is_known(dataset):
    out, _ = dataset
    for cell in all_cells(out):
        for label, pattern in REAL_LOOKING.items():
            assert not pattern.search(cell), label


def test_no_personal_data_outside_the_reserved_synthetic_ranges(dataset):
    out, _ = dataset
    emails = [c for c in all_cells(out) if "@" in c and "." in c.split("@")[-1]]
    assert emails and all(e.endswith("@example.invalid") for e in emails)


# ------------------------------------------------------------------ purpose / retention metadata


def test_metadata_has_deliberate_gaps_and_is_strictly_parseable(dataset, registry):
    out, m = dataset
    data = load_strict_yaml_mapping(out / "metadata" / "purpose_retention.yaml")
    assert set(data) == {"metadata_origin", "entries"}
    labels = load_labels(out / "ground_truth" / "labels.yaml", registry)
    positives = {(s.source_key, t, c) for s in labels.sources for t, c, _ in s.positives}
    seen, missing_purpose, missing_retention = set(), 0, 0
    for entry in data["entries"]:
        assert set(entry) <= {"source_key", "table", "column", "purpose", "retention"}
        key = (entry["source_key"], entry["table"], entry["column"])
        assert key in positives and key not in seen
        seen.add(key)
        assert "purpose" in entry or "retention" in entry  # an entry declares something
        missing_purpose += "purpose" not in entry
        missing_retention += "retention" not in entry
    no_entry = len(positives - seen)
    gaps = m["metadata_declarations_for_positive_columns"]
    assert (missing_purpose, missing_retention, no_entry) == (
        gaps["missing_purpose"], gaps["missing_retention"], gaps["no_entry"],
    )
    assert missing_purpose > 0 and missing_retention > 0 and no_entry > 0
    assert gaps["complete"] > 0
    assert sum(gaps.values()) == m["positives_total"]


def test_manifest_and_docs_are_consistent(dataset):
    out, m = dataset
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8")) == m
    doc = (out / "DATASET.md").read_text(encoding="utf-8")
    assert str(m["columns_total"]) in doc and str(m["seed"]) in doc
    assert "not general accuracy" in doc and "NOT_PERSONAL_DATA" in doc
