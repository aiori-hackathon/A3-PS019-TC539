"""Tests for fieldledger.cli — argument parsing, validation and end-to-end scans."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import textwrap
from pathlib import Path

import pytest

from fieldledger.cli import build_parser, main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv_file(path: Path, header: str, row: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{header}\n{row}\n", encoding="utf-8")


def _write_yaml(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _minimal_classes_yaml(path: Path) -> Path:
    p = path / "classes.yaml"
    _write_yaml(
        p,
        """\
        taxonomy_status: provisional
        classes:
          - id: ALPHA
            label: Alpha class
        """,
    )
    return p


def _minimal_rules_yaml(path: Path, registry_path: Path | None = None) -> Path:
    p = path / "rules.yaml"
    _write_yaml(
        p,
        """\
        rules_status: provisional
        rules:
          - id: R1
            class_id: ALPHA
            match: token
            patterns: [alpha]
        """,
    )
    return p


def _minimal_metadata_yaml(path: Path) -> Path:
    p = path / "meta.yaml"
    _write_yaml(
        p,
        """\
        metadata_origin: user_provided
        entries: []
        """,
    )
    return p


def _minimal_labels_yaml(path: Path, source_key: str = "data.csv") -> Path:
    p = path / "labels.yaml"
    _write_yaml(
        p,
        f"""\
        label_origin: team_authored_synthetic
        sources:
          - source_key: "{source_key}"
            positives:
              - {{table: data, column: alpha_col, class_id: ALPHA}}
            not_personal_data:
              - {{table: data, column: plain_col}}
        """,
    )
    return p


@pytest.fixture()
def workspace(tmp_path):
    """Create a minimal workspace with data, config and output dirs."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_csv_file(data_dir / "data.csv", "alpha_col,plain_col", "hello,world")

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    classes = _minimal_classes_yaml(config_dir)
    rules = _minimal_rules_yaml(config_dir)

    out_dir = tmp_path / "output"

    return {
        "tmp": tmp_path,
        "data": data_dir,
        "config": config_dir,
        "classes": classes,
        "rules": rules,
        "out": out_dir,
    }


# ---------------------------------------------------------------------------
# Parser / help tests
# ---------------------------------------------------------------------------


class TestParser:
    def test_help_exits_zero(self):
        code = main(["--help"])
        assert code == 0

    def test_scan_help(self):
        code = main(["scan", "--help"])
        assert code == 0

    def test_evaluate_help(self):
        code = main(["evaluate", "--help"])
        assert code == 0

    def test_version(self, capsys):
        code = main(["--version"])
        assert code == 0
        out = capsys.readouterr().out
        assert "fieldledger" in out

    def test_no_subcommand(self):
        code = main([])
        assert code == 2

    def test_scan_missing_required(self):
        code = main(["scan"])
        assert code == 2

    def test_evaluate_missing_required(self):
        code = main(["evaluate"])
        assert code == 2


# ---------------------------------------------------------------------------
# Argument validation tests
# ---------------------------------------------------------------------------


class TestArgValidation:
    def test_nonexistent_input(self, tmp_path, workspace, capsys):
        code = main([
            "scan",
            "--input", str(tmp_path / "nonexistent"),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
        ])
        assert code == 1
        err = capsys.readouterr().err
        assert "does not exist" in err

    def test_input_is_file_not_dir(self, workspace, capsys):
        f = workspace["data"] / "data.csv"
        code = main([
            "scan",
            "--input", str(f),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
        ])
        assert code == 1
        err = capsys.readouterr().err
        assert "directory" in err.lower()

    def test_bad_format(self, workspace, capsys):
        code = main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
            "--formats", "json,pdf",
        ])
        assert code == 1
        err = capsys.readouterr().err
        assert "unsupported" in err.lower()

    def test_nonexistent_classes(self, workspace, capsys):
        code = main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["tmp"] / "nope.yaml"),
            "--rules", str(workspace["rules"]),
        ])
        assert code == 1

    def test_nonexistent_rules(self, workspace, capsys):
        code = main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["tmp"] / "nope.yaml"),
        ])
        assert code == 1

    def test_evaluate_without_labels(self, workspace, capsys):
        code = main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
            "--evaluate",
        ])
        assert code == 1
        err = capsys.readouterr().err
        assert "labels" in err.lower()


# ---------------------------------------------------------------------------
# Successful scan tests
# ---------------------------------------------------------------------------


class TestScanSuccess:
    def test_basic_scan(self, workspace, capsys):
        code = main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
        ])
        assert code == 0
        out = capsys.readouterr().out
        assert "scan complete" in out

        # All three report files should exist
        assert (workspace["out"] / "fieldledger_report.json").is_file()
        assert (workspace["out"] / "fieldledger_inventory.csv").is_file()
        assert (workspace["out"] / "fieldledger_summary.md").is_file()

    def test_scan_json_content(self, workspace):
        main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
            "--formats", "json",
        ])
        data = json.loads(
            (workspace["out"] / "fieldledger_report.json").read_text(encoding="utf-8")
        )
        assert data["sources_scanned"] == 1
        assert data["total_findings"] >= 1
        # Relative path, no absolute
        for f in data["findings"]:
            assert ":\\" not in f["source_key"]

    def test_scan_csv_content(self, workspace):
        main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
            "--formats", "csv",
        ])
        text = (workspace["out"] / "fieldledger_inventory.csv").read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) >= 1
        assert all("source_key" in r for r in rows)

    def test_scan_single_format(self, workspace):
        main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
            "--formats", "md",
        ])
        assert (workspace["out"] / "fieldledger_summary.md").is_file()
        assert not (workspace["out"] / "fieldledger_report.json").exists()
        assert not (workspace["out"] / "fieldledger_inventory.csv").exists()

    def test_scan_with_metadata(self, workspace):
        meta_path = _minimal_metadata_yaml(workspace["config"])
        code = main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
            "--metadata", str(meta_path),
            "--formats", "json",
        ])
        assert code == 0
        data = json.loads(
            (workspace["out"] / "fieldledger_report.json").read_text(encoding="utf-8")
        )
        assert "metadata" in data

    def test_scan_no_declarations_by_default(self, workspace):
        meta_path = _minimal_metadata_yaml(workspace["config"])
        main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
            "--metadata", str(meta_path),
            "--formats", "json",
        ])
        data = json.loads(
            (workspace["out"] / "fieldledger_report.json").read_text(encoding="utf-8")
        )
        assert data.get("declarations_included") is False

    def test_scan_empty_dir(self, tmp_path, workspace):
        empty = tmp_path / "empty"
        empty.mkdir()
        code = main([
            "scan",
            "--input", str(empty),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
            "--formats", "json",
        ])
        assert code == 0
        data = json.loads(
            (workspace["out"] / "fieldledger_report.json").read_text(encoding="utf-8")
        )
        assert data["total_findings"] == 0


# ---------------------------------------------------------------------------
# Evaluation tests
# ---------------------------------------------------------------------------


class TestEvaluation:
    def test_evaluate_subcommand(self, workspace, capsys):
        labels = _minimal_labels_yaml(workspace["config"])
        code = main([
            "evaluate",
            "--input", str(workspace["data"]),
            "--labels", str(labels),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
        ])
        assert code == 0
        out = capsys.readouterr().out
        assert "evaluation complete" in out
        assert (workspace["out"] / "fieldledger_evaluation.json").is_file()

    def test_evaluate_content(self, workspace):
        labels = _minimal_labels_yaml(workspace["config"])
        main([
            "evaluate",
            "--input", str(workspace["data"]),
            "--labels", str(labels),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
        ])
        data = json.loads(
            (workspace["out"] / "fieldledger_evaluation.json").read_text(encoding="utf-8")
        )
        assert "scope_notice" in data
        assert "classes" in data
        assert data["label_origin"] == "team_authored_synthetic"
        # No absolute paths
        text = json.dumps(data)
        assert ":\\" not in text

    def test_scan_with_evaluate(self, workspace, capsys):
        labels = _minimal_labels_yaml(workspace["config"])
        code = main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
            "--evaluate",
            "--labels", str(labels),
        ])
        assert code == 0
        # Both scan reports and evaluation report should exist
        assert (workspace["out"] / "fieldledger_report.json").is_file()
        assert (workspace["out"] / "fieldledger_evaluation.json").is_file()

    def test_no_evaluation_without_flag(self, workspace):
        main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
        ])
        assert not (workspace["out"] / "fieldledger_evaluation.json").exists()


# ---------------------------------------------------------------------------
# Exit code tests
# ---------------------------------------------------------------------------


class TestExitCodes:
    def test_success_is_zero(self, workspace):
        code = main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
        ])
        assert code == 0

    def test_usage_error_is_two(self):
        code = main([])
        assert code == 2

    def test_operational_error_is_one(self, workspace, capsys):
        code = main([
            "scan",
            "--input", str(workspace["tmp"] / "nonexistent"),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
        ])
        assert code == 1


# ---------------------------------------------------------------------------
# Output directory tests
# ---------------------------------------------------------------------------


class TestOutputDir:
    def test_creates_output_dir(self, workspace):
        out = workspace["out"] / "nested" / "dir"
        assert not out.exists()
        code = main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(out),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
        ])
        assert code == 0
        assert out.is_dir()

    def test_existing_output_dir_ok(self, workspace):
        workspace["out"].mkdir(parents=True, exist_ok=True)
        code = main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
        ])
        assert code == 0


# ---------------------------------------------------------------------------
# Include declarations tests
# ---------------------------------------------------------------------------


class TestIncludeDeclarations:
    def test_default_no_declarations(self, workspace):
        meta_path = _minimal_metadata_yaml(workspace["config"])
        main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
            "--metadata", str(meta_path),
            "--formats", "json",
        ])
        data = json.loads(
            (workspace["out"] / "fieldledger_report.json").read_text(encoding="utf-8")
        )
        assert data["declarations_included"] is False

    def test_include_declarations_flag(self, workspace):
        meta_path = _minimal_metadata_yaml(workspace["config"])
        main([
            "scan",
            "--input", str(workspace["data"]),
            "--out", str(workspace["out"]),
            "--classes", str(workspace["classes"]),
            "--rules", str(workspace["rules"]),
            "--metadata", str(meta_path),
            "--formats", "json",
            "--include-declarations",
        ])
        data = json.loads(
            (workspace["out"] / "fieldledger_report.json").read_text(encoding="utf-8")
        )
        assert data["declarations_included"] is True
