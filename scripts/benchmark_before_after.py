"""Reproducible before/after comparison of two rules files on the bundled synthetic benchmark.

    python scripts/benchmark_before_after.py \
        --baseline-rules tests/fixtures/rules_baseline_d3ab9c2.yaml --rules config/rules.yaml

Reads column NAMES only (via the normal discovery/profiling/detection/evaluation code) and the
committed labels; it modifies nothing. The benchmark is team-authored and synthetic, and the
current rules were tuned after inspecting it, so the 'after' figures are IN-SAMPLE and are not an
accuracy estimate. Use scripts/heldout_probe.py for the exploratory out-of-sample check.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from fieldledger.classes import load_class_registry  # noqa: E402
from fieldledger.detector import ambiguous_columns, detect_candidates  # noqa: E402
from fieldledger.discovery import discover_sources  # noqa: E402
from fieldledger.evaluation import evaluate, load_labels  # noqa: E402
from fieldledger.findings import source_key_for  # noqa: E402
from fieldledger.profiler import profile_source  # noqa: E402
from fieldledger.rules import load_rule_set  # noqa: E402


def run(rules_path: Path, registry, labels, base: Path):
    ruleset = load_rule_set(rules_path, registry)
    profiles, findings = {}, []
    for path in discover_sources(base):
        key = source_key_for(path, base)
        profiles[key] = profile_source(path, base)
        findings.extend(detect_candidates(profiles[key], ruleset, source_key=key))
    report = evaluate(findings, labels, profiles, registry=registry, ruleset=ruleset)
    return findings, report


def totals(report):
    tp = sum(m.true_positives for m in report.per_class)
    fp = sum(m.false_positives for m in report.per_class)
    fn = sum(m.false_negatives for m in report.per_class)
    p, r = tp / (tp + fp), tp / (tp + fn)
    return tp, fp, fn, p, r, 2 * p * r / (p + r)


def show(title, findings, report):
    tp, fp, fn, p, r, f1 = totals(report)
    print(f"== {title}")
    print(f"{'class':26s} {'TP':>4s} {'FP':>4s} {'FN':>4s} {'prec':>7s} {'recall':>7s}")
    for m in report.per_class:
        print(
            f"{m.class_id:26s} {m.true_positives:4d} {m.false_positives:4d} {m.false_negatives:4d} "
            f"{m.precision:7.3f} {m.recall:7.3f}"
        )
    print(f"{'ALL (micro, derived)':26s} {tp:4d} {fp:4d} {fn:4d} {p:7.3f} {r:7.3f}   F1={f1:.3f}")
    print(f"findings={len(findings)}  ambiguous columns={[a.column_name for a in ambiguous_columns(findings)]}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline-rules", type=Path, required=True)
    ap.add_argument("--rules", type=Path, default=REPO_ROOT / "config" / "rules.yaml")
    ap.add_argument("--classes", type=Path, default=REPO_ROOT / "config" / "classes.yaml")
    ap.add_argument("--data", type=Path, default=REPO_ROOT / "data" / "synthetic")
    args = ap.parse_args(argv)

    registry = load_class_registry(args.classes)
    labels = load_labels(args.data / "ground_truth" / "labels.yaml", registry)
    old_f, old_r = run(args.baseline_rules, registry, labels, args.data)
    new_f, new_r = run(args.rules, registry, labels, args.data)
    print("IN-SAMPLE synthetic benchmark (team-authored); not real-world accuracy.\n")
    show(f"BEFORE {args.baseline_rules.name}", old_f, old_r)
    show(f"AFTER  {args.rules.name}", new_f, new_r)

    old = {f.identity: f for f in old_f}
    new = {f.identity: f for f in new_f}
    truth = {
        (s.source_key, t, c): cls for s in labels.sources for (t, c, cls) in s.positives
    }

    def verdict(identity):
        return "TP" if truth.get(identity[:3]) == identity[3] else "FP"

    print("== Findings REMOVED by the new rules (identity: previous verdict)")
    for k in sorted(set(old) - set(new)):
        print(f"  - {k[0]}:{k[1]}.{k[2]} [{k[3]}] was {verdict(k)} rules={list(old[k].rule_ids)}")
    print("\n== Findings ADDED by the new rules (identity: verdict)")
    for k in sorted(set(new) - set(old)):
        print(f"  + {k[0]}:{k[1]}.{k[2]} [{k[3]}] {verdict(k)} rules={list(new[k].rule_ids)}")
    print("\n== Findings whose explaining rule ids changed, same class")
    for k in sorted(set(old) & set(new)):
        if old[k].rule_ids != new[k].rule_ids:
            print(f"  ~ {k[0]}:{k[1]}.{k[2]} [{k[3]}] {list(old[k].rule_ids)} -> {list(new[k].rule_ids)}")
    print("\n== Remaining errors with the new rules")
    for k in sorted(new):
        if verdict(k) == "FP":
            print(f"  FP {k[0]}:{k[1]}.{k[2]} [{k[3]}] rules={list(new[k].rule_ids)} label={truth.get(k[:3]) or 'NOT_PERSONAL_DATA'}")
    for key, cls in sorted(truth.items()):
        if (*key, cls) not in new:
            print(f"  FN {key[0]}:{key[1]}.{key[2]} [{cls}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
