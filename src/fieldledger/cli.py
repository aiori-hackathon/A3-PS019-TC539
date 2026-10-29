"""Command-line interface for FieldLedger.

Subcommands
-----------
scan      Discover, profile and detect personal-data candidates in a data directory.
evaluate  Score findings against ground-truth labels (explicit evaluation only).

Exit codes: 0 success, 2 argument error (argparse), 1 operational error.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from . import __version__
from .classes import ClassRegistry, load_class_registry
from .detector import detect_candidates
from .discovery import discover_sources
from .errors import ConfigError, FieldLedgerError
from .evaluation import EvaluationReport, evaluate, load_labels
from .findings import Finding, source_key_for
from .metadata import GapReport, load_metadata, link_metadata
from .profiler import profile_source
from .profiles import SourceProfile
from .report import write_reports, write_evaluation_report
from .rules import RuleSet, load_rule_set

logger = logging.getLogger(__name__)

_DEFAULT_CLASSES = "config/classes.yaml"
_DEFAULT_RULES = "config/rules.yaml"
_DEFAULT_FORMATS = "json,csv,md"


def _resolve_path(raw: str, label: str) -> Path | None:
    """Return a resolved Path, or None (after printing an error) if it fails."""
    p = Path(raw)
    try:
        resolved = p.resolve(strict=True)
    except OSError:
        print(f"error: {label} does not exist or is not accessible: {p}", file=sys.stderr)
        return None
    return resolved


def _load_config(
    classes_path: Path,
    rules_path: Path,
) -> tuple[ClassRegistry, RuleSet]:
    """Load class registry and rule set, raising SystemExit on config errors."""
    try:
        registry = load_class_registry(classes_path)
    except ConfigError as exc:
        print(f"error: classes configuration: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    try:
        ruleset = load_rule_set(rules_path, registry)
    except ConfigError as exc:
        print(f"error: rules configuration: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    return registry, ruleset


def _run_scan(
    input_dir: Path,
    registry: ClassRegistry,
    ruleset: RuleSet,
) -> tuple[tuple[Finding, ...], dict[str, SourceProfile]]:
    """Discover, profile and detect candidates. Returns (all_findings, profiles_by_key)."""
    sources = discover_sources(input_dir)
    if not sources:
        logger.info("no supported sources found in %s", input_dir.name)

    profiles_by_key: dict[str, SourceProfile] = {}
    all_findings: list[Finding] = []

    for source_path in sources:
        try:
            profile = profile_source(source_path, input_dir)
            key = source_key_for(source_path, input_dir)
        except FieldLedgerError as exc:
            logger.warning("skipping source: %s", exc)
            continue

        profiles_by_key[key] = profile
        findings = detect_candidates(profile, ruleset, source_key=key)
        all_findings.extend(findings)
        logger.info(
            "profiled %s: %d table(s), %d finding(s)",
            key,
            len(profile.tables),
            len(findings),
        )

    return tuple(all_findings), profiles_by_key


def _cmd_scan(args: argparse.Namespace) -> int:
    """Execute the 'scan' subcommand."""
    input_dir = _resolve_path(args.input, "--input directory")

    if input_dir is None:
        return 1

    if not input_dir.is_dir():
        print(f"error: --input must be a directory: {input_dir}", file=sys.stderr)
        return 1

    classes_path = _resolve_path(args.classes, "--classes file")
    if classes_path is None:
        return 1

    rules_path = _resolve_path(args.rules, "--rules file")
    if rules_path is None:
        return 1

    registry, ruleset = _load_config(classes_path, rules_path)
    # Parse formats
    formats = [f.strip().lower() for f in args.formats.split(",")]
    valid_formats = {"json", "csv", "md"}
    invalid = set(formats) - valid_formats
    if invalid:
        print(f"error: unsupported format(s): {sorted(invalid)}", file=sys.stderr)
        return 1

    # Create output directory
    out_dir = Path(args.out)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"error: cannot create output directory: {exc}", file=sys.stderr)
        return 1

    # Run the scan pipeline
    try:
        all_findings, profiles_by_key = _run_scan(input_dir, registry, ruleset)
    except FieldLedgerError as exc:
        print(f"error: scan failed: {exc}", file=sys.stderr)
        return 1

    # Load metadata if supplied
    gap_report: GapReport | None = None
    if args.metadata:
        meta_path = _resolve_path(args.metadata, "--metadata file")
        try:
            metadata_set = load_metadata(meta_path)
            gap_report = link_metadata(
                all_findings,
                metadata_set,
                profiles_by_key,
                registry=registry,
                ruleset=ruleset,
            )
        except (ConfigError, ValueError, TypeError) as exc:
            print(f"error: metadata processing: {exc}", file=sys.stderr)
            return 1

    # Write reports
    try:
        written = write_reports(
            findings=all_findings,
            profiles_by_key=profiles_by_key,
            gap_report=gap_report,
            registry=registry,
            ruleset=ruleset,
            out_dir=out_dir,
            formats=formats,
            include_declarations=args.include_declarations,
        )
    except OSError as exc:
        print(f"error: writing reports: {exc}", file=sys.stderr)
        return 1

    for path in written:
        print(f"wrote {path}")

    # Optional evaluation
    if args.evaluate:
        if not args.labels:
            print("error: --labels is required with --evaluate", file=sys.stderr)
            return 1
        labels_path = _resolve_path(args.labels, "--labels file")
        try:
            label_set = load_labels(labels_path, registry)
            eval_report = evaluate(
                all_findings,
                label_set,
                profiles_by_key,
                registry=registry,
                ruleset=ruleset,
            )
        except (ConfigError, ValueError, TypeError) as exc:
            print(f"error: evaluation failed: {exc}", file=sys.stderr)
            return 1

        try:
            eval_path = write_evaluation_report(eval_report, out_dir)
        except OSError as exc:
            print(f"error: writing evaluation report: {exc}", file=sys.stderr)
            return 1

        print(f"wrote {eval_path}")

    total = len(all_findings)
    sources_count = len(profiles_by_key)
    print(f"scan complete: {sources_count} source(s), {total} finding(s)")
    return 0


def _cmd_evaluate(args: argparse.Namespace) -> int:
    """Execute the 'evaluate' subcommand."""
    input_dir = _resolve_path(args.input, "--input directory")

    if not input_dir.is_dir():
        print(f"error: --input must be a directory: {args.input}", file=sys.stderr)
        return 1

    classes_path = _resolve_path(args.classes, "--classes file")
    if classes_path is None:
        return 1

    rules_path = _resolve_path(args.rules, "--rules file")
    if rules_path is None:
        return 1

    labels_path = _resolve_path(args.labels, "--labels file")
    if labels_path is None:
        return 1

    registry, ruleset = _load_config(classes_path, rules_path)

    out_dir = Path(args.out)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"error: cannot create output directory: {exc}", file=sys.stderr)
        return 1

    # Run the scan pipeline
    try:
        all_findings, profiles_by_key = _run_scan(input_dir, registry, ruleset)
    except FieldLedgerError as exc:
        print(f"error: scan failed: {exc}", file=sys.stderr)
        return 1

    # Load labels and evaluate
    try:
        label_set = load_labels(labels_path, registry)
        eval_report = evaluate(
            all_findings,
            label_set,
            profiles_by_key,
            registry=registry,
            ruleset=ruleset,
        )
    except (ConfigError, ValueError, TypeError) as exc:
        print(f"error: evaluation failed: {exc}", file=sys.stderr)
        return 1

    try:
        eval_path = write_evaluation_report(eval_report, out_dir)
    except OSError as exc:
        print(f"error: writing evaluation report: {exc}", file=sys.stderr)
        return 1

    print(f"wrote {eval_path}")
    total = len(all_findings)
    sources_count = len(profiles_by_key)
    print(f"evaluation complete: {sources_count} source(s), {total} finding(s)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser (also used by tests)."""
    parser = argparse.ArgumentParser(
        prog="fieldledger",
        description=(
            "FieldLedger: configurable personal-data field discovery. "
            "Technical evidence for human review — not a legal determination."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"fieldledger {__version__}",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ---- scan ----
    scan_p = sub.add_parser(
        "scan",
        help="Discover, profile and detect personal-data candidates.",
    )
    scan_p.add_argument("--input", required=True, help="Directory containing data sources.")
    scan_p.add_argument("--metadata", default=None, help="Purpose/retention metadata YAML.")
    scan_p.add_argument("--out", required=True, help="Output directory for reports.")
    scan_p.add_argument("--classes", default=_DEFAULT_CLASSES, help="Class registry YAML.")
    scan_p.add_argument("--rules", default=_DEFAULT_RULES, help="Rules YAML.")
    scan_p.add_argument(
        "--formats",
        default=_DEFAULT_FORMATS,
        help="Comma-separated report formats: json, csv, md (default: json,csv,md).",
    )
    scan_p.add_argument(
        "--include-declarations",
        action="store_true",
        default=False,
        help="Include purpose/retention text in reports (privacy-sensitive).",
    )
    scan_p.add_argument(
        "--evaluate",
        action="store_true",
        default=False,
        help="Also run evaluation after scanning.",
    )
    scan_p.add_argument("--labels", default=None, help="Ground-truth labels YAML (for --evaluate).")
    scan_p.set_defaults(func=_cmd_scan)

    # ---- evaluate ----
    eval_p = sub.add_parser(
        "evaluate",
        help="Score findings against ground-truth labels.",
    )
    eval_p.add_argument("--input", required=True, help="Directory containing data sources.")
    eval_p.add_argument("--labels", required=True, help="Ground-truth labels YAML.")
    eval_p.add_argument("--out", default=".", help="Output directory (default: current dir).")
    eval_p.add_argument("--classes", default=_DEFAULT_CLASSES, help="Class registry YAML.")
    eval_p.add_argument("--rules", default=_DEFAULT_RULES, help="Rules YAML.")
    eval_p.set_defaults(func=_cmd_evaluate)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and dispatch to the appropriate subcommand.

    Returns an exit code: 0 for success, 1 for operational errors,
    2 for argument/usage errors (raised by argparse).
    """
    logging.basicConfig(
        format="%(levelname)s: %(message)s",
        level=logging.INFO,
    )

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse calls sys.exit(2) on usage errors; capture and return the code.
        return exc.code if isinstance(exc.code, int) else 2

    return args.func(args)
