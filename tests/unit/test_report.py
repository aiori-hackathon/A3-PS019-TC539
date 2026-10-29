"""Tests for fieldledger.report — JSON, CSV and Markdown report generation."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from fieldledger.classes import ClassDef, ClassRegistry
from fieldledger.evaluation import ClassMetrics, EvaluationReport
from fieldledger.detector import AmbiguousColumn
from fieldledger.findings import EVIDENCE_NOTICE, Finding
from fieldledger.metadata import (
    DeclarationStatus,
    GAP_NOTICE,
    GapReport,
    GapSummary,
    LinkedField,
    MetadataEntry,
    MetadataSet,
    UnmatchedEntry,
    UnmatchedReason,
    link_metadata,
)
from fieldledger.profiles import (
    BroadType,
    ColumnProfile,
    SourceProfile,
    SourceType,
    TableProfile,
    TypeBasis,
)
from fieldledger.report import (
    write_evaluation_report,
    write_reports,
)
from fieldledger.rules import Rule, RuleSet


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry():
    return ClassRegistry(
        taxonomy_status="confirmed",
        classes=(
            ClassDef(id="ALPHA", label="Alpha"),
            ClassDef(id="BETA", label="Beta"),
        ),
    )


@pytest.fixture()
def ruleset():
    return RuleSet(
        rules_status="provisional",
        rules=(
            Rule(id="R1", class_id="ALPHA", match="token", patterns=(("alpha",),)),
            Rule(id="R2", class_id="BETA", match="token", patterns=(("beta",),)),
        ),
    )


def _make_profile(table="t", columns=("alpha_col", "beta_col", "plain_col"), name="a.csv"):
    cols = tuple(
        ColumnProfile(name=c, position=i + 1, inferred_type=BroadType.STRING)
        for i, c in enumerate(columns)
    )
    return SourceProfile(
        source_type=SourceType.CSV,
        source_name=name,
        type_basis=TypeBasis.OBSERVED_VALUES,
        tables=(TableProfile(name=table, columns=cols, row_count=10),),
    )


def _make_finding(source="a.csv", table="t", column="alpha_col", pos=1, cls="ALPHA", rules=("R1",)):
    return Finding(
        source_key=source,
        table_name=table,
        column_name=column,
        column_position=pos,
        class_id=cls,
        rule_ids=rules,
    )


@pytest.fixture()
def sample_findings():
    return (
        _make_finding(column="alpha_col", pos=1, cls="ALPHA", rules=("R1",)),
        _make_finding(column="beta_col", pos=2, cls="BETA", rules=("R2",)),
    )


@pytest.fixture()
def sample_profiles():
    return {"a.csv": _make_profile()}


@pytest.fixture()
def sample_gap_report(sample_findings, sample_profiles, registry, ruleset):
    meta = MetadataSet(
        metadata_origin="user_provided",
        entries=(
            MetadataEntry(
                source_key="a.csv",
                table_name="t",
                column_name="alpha_col",
                purpose="testing",
                retention="30 days",
            ),
        ),
    )
    return link_metadata(sample_findings, meta, sample_profiles, registry=registry, ruleset=ruleset)


# ---------------------------------------------------------------------------
# JSON report tests
# ---------------------------------------------------------------------------


class TestJsonReport:
    def test_basic_structure(self, tmp_path, sample_findings, sample_profiles, registry, ruleset):
        written = write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=None,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["json"],
        )
        assert len(written) == 1
        assert written[0].name == "fieldledger_report.json"
        data = json.loads(written[0].read_text(encoding="utf-8"))
        assert data["sources_scanned"] == 1
        assert data["total_findings"] == 2
        assert len(data["findings"]) == 2
        assert "technical_evidence_notice" in data
        assert data["taxonomy_status"] == "confirmed"
        assert data["rules_status"] == "provisional"

    def test_no_absolute_paths(self, tmp_path, sample_findings, sample_profiles, registry, ruleset):
        write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=None,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["json"],
        )
        text = (tmp_path / "fieldledger_report.json").read_text(encoding="utf-8")
        # Must not contain any Windows or Unix absolute paths
        assert ":\\" not in text
        assert '":/' not in text or '"://' not in text  # allow URL-like but not paths

    def test_with_metadata(self, tmp_path, sample_findings, sample_profiles, sample_gap_report, registry, ruleset):
        written = write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=sample_gap_report,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["json"],
        )
        data = json.loads(written[0].read_text(encoding="utf-8"))
        assert "metadata" in data
        assert data["declarations_included"] is False
        # Purpose/retention text must NOT appear by default
        for lf in data["metadata"]["linked_fields"]:
            assert "purpose" not in lf
            assert "retention" not in lf

    def test_with_declarations(self, tmp_path, sample_findings, sample_profiles, sample_gap_report, registry, ruleset):
        written = write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=sample_gap_report,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["json"],
            include_declarations=True,
        )
        data = json.loads(written[0].read_text(encoding="utf-8"))
        assert data["declarations_included"] is True
        # At least one linked field should have declaration text
        has_purpose = any(
            lf.get("purpose") is not None for lf in data["metadata"]["linked_fields"]
        )
        assert has_purpose

    def test_empty_findings(self, tmp_path, registry, ruleset):
        written = write_reports(
            findings=(),
            profiles_by_key={},
            gap_report=None,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["json"],
        )
        data = json.loads(written[0].read_text(encoding="utf-8"))
        assert data["total_findings"] == 0
        assert data["sources_scanned"] == 0
        assert data["findings"] == []


# ---------------------------------------------------------------------------
# CSV report tests
# ---------------------------------------------------------------------------


class TestCsvReport:
    def test_basic_csv(self, tmp_path, sample_findings, sample_profiles, registry, ruleset):
        written = write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=None,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["csv"],
        )
        assert len(written) == 1
        assert written[0].name == "fieldledger_inventory.csv"
        text = written[0].read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["source_key"] == "a.csv"
        assert rows[0]["class_id"] == "ALPHA"

    def test_csv_with_metadata(self, tmp_path, sample_findings, sample_profiles, sample_gap_report, registry, ruleset):
        written = write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=sample_gap_report,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["csv"],
        )
        text = written[0].read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        # Must have status columns
        assert "status" in rows[0]
        assert "has_purpose" in rows[0]
        # Must NOT have declaration text by default
        assert "purpose" not in rows[0]
        assert "retention" not in rows[0]

    def test_csv_with_declarations(self, tmp_path, sample_findings, sample_profiles, sample_gap_report, registry, ruleset):
        written = write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=sample_gap_report,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["csv"],
            include_declarations=True,
        )
        text = written[0].read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert "purpose" in rows[0]
        assert "retention" in rows[0]

    def test_csv_no_absolute_paths(self, tmp_path, sample_findings, sample_profiles, registry, ruleset):
        write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=None,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["csv"],
        )
        text = (tmp_path / "fieldledger_inventory.csv").read_text(encoding="utf-8")
        assert ":\\" not in text

    def test_csv_empty(self, tmp_path, registry, ruleset):
        written = write_reports(
            findings=(),
            profiles_by_key={},
            gap_report=None,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["csv"],
        )
        text = written[0].read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        assert rows == []


# ---------------------------------------------------------------------------
# Markdown report tests
# ---------------------------------------------------------------------------


class TestMarkdownReport:
    def test_basic_md(self, tmp_path, sample_findings, sample_profiles, registry, ruleset):
        written = write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=None,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["md"],
        )
        assert len(written) == 1
        assert written[0].name == "fieldledger_summary.md"
        text = written[0].read_text(encoding="utf-8")
        assert "# FieldLedger Scan Summary" in text
        assert "Sources scanned" in text
        assert "Total findings" in text
        assert EVIDENCE_NOTICE in text

    def test_md_class_table(self, tmp_path, sample_findings, sample_profiles, registry, ruleset):
        write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=None,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["md"],
        )
        text = (tmp_path / "fieldledger_summary.md").read_text(encoding="utf-8")
        assert "ALPHA" in text
        assert "BETA" in text

    def test_md_with_metadata(self, tmp_path, sample_findings, sample_profiles, sample_gap_report, registry, ruleset):
        write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=sample_gap_report,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["md"],
        )
        text = (tmp_path / "fieldledger_summary.md").read_text(encoding="utf-8")
        assert "Metadata gap summary" in text
        assert GAP_NOTICE in text

    def test_md_empty(self, tmp_path, registry, ruleset):
        write_reports(
            findings=(),
            profiles_by_key={},
            gap_report=None,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["md"],
        )
        text = (tmp_path / "fieldledger_summary.md").read_text(encoding="utf-8")
        assert "Total findings**: 0" in text
        assert "Sources scanned**: 0" in text

    def test_md_no_absolute_paths(self, tmp_path, sample_findings, sample_profiles, registry, ruleset):
        write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=None,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["md"],
        )
        text = (tmp_path / "fieldledger_summary.md").read_text(encoding="utf-8")
        assert ":\\" not in text


# ---------------------------------------------------------------------------
# Multiple formats
# ---------------------------------------------------------------------------


class TestMultipleFormats:
    def test_all_formats(self, tmp_path, sample_findings, sample_profiles, registry, ruleset):
        written = write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=None,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["json", "csv", "md"],
        )
        names = {p.name for p in written}
        assert names == {"fieldledger_report.json", "fieldledger_inventory.csv", "fieldledger_summary.md"}

    def test_single_format(self, tmp_path, sample_findings, sample_profiles, registry, ruleset):
        written = write_reports(
            findings=sample_findings,
            profiles_by_key=sample_profiles,
            gap_report=None,
            registry=registry,
            ruleset=ruleset,
            out_dir=tmp_path,
            formats=["csv"],
        )
        assert len(written) == 1
        assert written[0].name == "fieldledger_inventory.csv"


# ---------------------------------------------------------------------------
# Evaluation report tests
# ---------------------------------------------------------------------------


class TestEvaluationReport:
    def test_evaluation_json(self, tmp_path):
        report = EvaluationReport(
            taxonomy_status="confirmed",
            rules_status="provisional",
            label_origin="team_authored_synthetic",
            scope_notice="Test scope notice.",
            labelled_source_count=1,
            labelled_column_count=3,
            unscored_finding_count=0,
            ambiguous_columns=(),
            per_class=(
                ClassMetrics(
                    class_id="ALPHA",
                    true_positives=2,
                    false_positives=1,
                    false_negatives=0,
                    precision=2 / 3,
                    recall=1.0,
                ),
            ),
        )
        path = write_evaluation_report(report, tmp_path)
        assert path.name == "fieldledger_evaluation.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["taxonomy_status"] == "confirmed"
        assert data["rules_status"] == "provisional"
        assert len(data["classes"]) == 1
        assert data["classes"][0]["true_positives"] == 2
        assert data["scope_notice"] == "Test scope notice."

    def test_evaluation_no_absolute_paths(self, tmp_path):
        report = EvaluationReport(
            taxonomy_status="confirmed",
            rules_status="provisional",
            label_origin="team_authored_synthetic",
            scope_notice="scope.",
            labelled_source_count=0,
            labelled_column_count=0,
            unscored_finding_count=0,
            ambiguous_columns=(),
            per_class=(),
        )
        path = write_evaluation_report(report, tmp_path)
        text = path.read_text(encoding="utf-8")
        assert ":\\" not in text
