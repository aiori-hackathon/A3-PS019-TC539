"""Exploratory held-out probe for the column-name rules (NOT an independently validated benchmark).

Scores one rules file (and optionally a baseline rules file) against the labelled column names in
tests/fixtures/heldout_probe/probe_columns.yaml. Only column NAMES are used; no data is read.

    python scripts/heldout_probe.py --rules config/rules.yaml \
        --baseline-rules tests/fixtures/rules_baseline_d3ab9c2.yaml [--list-errors]

The probe was written by the engineer who wrote the rules (after the rules were frozen), so it is
exploratory. Per-class and overall figures follow evaluation.py: for each class,
TP = labelled C and predicted C, FP = predicted C but not labelled C, FN = labelled C not predicted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from fieldledger.classes import load_class_registry  # noqa: E402
from fieldledger.detector import detect_candidates  # noqa: E402
from fieldledger.profiles import (  # noqa: E402
    BroadType,
    ColumnProfile,
    SourceProfile,
    SourceType,
    TableProfile,
    TypeBasis,
)
from fieldledger.rules import load_rule_set  # noqa: E402
from fieldledger.strict_yaml import load_strict_yaml_mapping  # noqa: E402

NOT_PERSONAL = "NOT_PERSONAL_DATA"
DEFAULT_PROBE = REPO_ROOT / "tests" / "fixtures" / "heldout_probe" / "probe_columns.yaml"


def load_probe(path: Path) -> list[dict]:
    data = load_strict_yaml_mapping(path)
    cols = data["columns"]
    names = [c["name"] for c in cols]
    if len(set(names)) != len(names):
        raise SystemExit("probe contains duplicate names")
    return cols


def predict(ruleset, name: str) -> set[str]:
    profile = SourceProfile(
        SourceType.CSV,
        "p.csv",
        TypeBasis.OBSERVED_VALUES,
        (TableProfile("t", (ColumnProfile(name, 1, BroadType.STRING, 1, 0),), 1),),
    )
    return {f.class_id for f in detect_candidates(profile, ruleset, source_key="p.csv")}


def score(ruleset, columns: list[dict], class_ids: tuple[str, ...]) -> dict:
    per = {c: {"tp": 0, "fp": 0, "fn": 0} for c in class_ids}
    errors = []
    for col in columns:
        got = predict(ruleset, col["name"])
        want = None if col["label"] == NOT_PERSONAL else col["label"]
        for c in got:
            if c == want:
                per[c]["tp"] += 1
            else:
                per[c]["fp"] += 1
                errors.append(("FP", col["name"], c, col["label"], col["kind"]))
        if want and want not in got:
            per[want]["fn"] += 1
            errors.append(("FN", col["name"], want, col["label"], col["kind"]))
    return {"per_class": per, "errors": errors}


def ratio(n: int, d: int) -> float | None:
    return n / d if d else None


def fmt(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def report(title: str, result: dict) -> None:
    per = result["per_class"]
    tp = sum(v["tp"] for v in per.values())
    fp = sum(v["fp"] for v in per.values())
    fn = sum(v["fn"] for v in per.values())
    p, r = ratio(tp, tp + fp), ratio(tp, tp + fn)
    f1 = None if p is None or r is None or p + r == 0 else 2 * p * r / (p + r)
    print(f"== {title}")
    print(f"{'class':26s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'prec':>6s} {'recall':>6s}")
    for c, v in per.items():
        print(
            f"{c:26s} {v['tp']:4d} {v['fp']:4d} {v['fn']:4d} "
            f"{fmt(ratio(v['tp'], v['tp'] + v['fp'])):>6s} {fmt(ratio(v['tp'], v['tp'] + v['fn'])):>6s}"
        )
    print(f"{'ALL (micro, derived)':26s} {tp:4d} {fp:4d} {fn:4d} {fmt(p):>6s} {fmt(r):>6s}   F1={fmt(f1)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rules", type=Path, default=REPO_ROOT / "config" / "rules.yaml")
    ap.add_argument("--baseline-rules", type=Path, default=None)
    ap.add_argument("--classes", type=Path, default=REPO_ROOT / "config" / "classes.yaml")
    ap.add_argument("--probe", type=Path, default=DEFAULT_PROBE)
    ap.add_argument("--list-errors", action="store_true")
    args = ap.parse_args(argv)

    registry = load_class_registry(args.classes)
    class_ids = tuple(registry.ids())
    columns = load_probe(args.probe)
    pos = sum(1 for c in columns if c["label"] != NOT_PERSONAL)
    print(f"EXPLORATORY PROBE: {len(columns)} names ({pos} personal-data, {len(columns) - pos} not).")
    print("Author-labelled after rules were frozen; not an independently validated benchmark.\n")

    runs = []
    if args.baseline_rules:
        runs.append(("BASELINE rules " + args.baseline_rules.name, args.baseline_rules))
    runs.append(("CURRENT rules " + args.rules.name, args.rules))
    for title, path in runs:
        result = score(load_rule_set(path, registry), columns, class_ids)
        report(title, result)
        if args.list_errors:
            for kind, name, cls, label, ck in sorted(result["errors"]):
                print(f"   {kind} {name:32s} class={cls:24s} label={label} ({ck})")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
