"""Contract tests for the FieldLedger web API (docs/api-contract.md, v1).

These run WITHOUT the optional web dependencies. They check that:
  * the human-readable contract, the constants in fieldledger.web.contract and the real backend
    (enums, limits, config, report writer) agree;
  * the web extra stays optional and the core CLI does not depend on it;
  * .gitignore protects user data and build output without hiding the team's other work.
They do not test the API implementation, which does not exist yet.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fieldledger import errors as backend_errors
from fieldledger.classes import MAX_CONFIG_BYTES, load_class_registry
from fieldledger.detector import detect_candidates
from fieldledger.discovery import SUPPORTED_SUFFIXES as BACKEND_SUFFIXES
from fieldledger.discovery import discover_sources
from fieldledger.findings import source_key_for
from fieldledger.limits import DEFAULT_LIMITS
from fieldledger.metadata import METADATA_ORIGINS as BACKEND_ORIGINS
from fieldledger.metadata import DeclarationStatus
from fieldledger.profiler import profile_source
from fieldledger.report import write_reports
from fieldledger.rules import load_rule_set
from fieldledger.web import contract

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "api-contract.md"
SYNTHETIC = ROOT / "data" / "synthetic"


# ---------------------------------------------------------------- helpers


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _block(name: str) -> str:
    m = re.search(
        rf"<!-- contract:{re.escape(name)}:start -->\n(.*?)<!-- contract:{re.escape(name)}:end -->",
        _doc(),
        re.DOTALL,
    )
    assert m, f"marker block {name!r} missing from docs/api-contract.md"
    return m.group(1)


def _bullets(name: str) -> list[str]:
    return re.findall(r"^- `([^`]+)`", _block(name), re.MULTILINE)


def _kv(name: str) -> dict[str, float]:
    return {
        k: float(v)
        for k, v in re.findall(r"^- `(\w+)` = ([0-9]+(?:\.[0-9]+)?)", _block(name), re.MULTILINE)
    }


def _real_findings():
    registry = load_class_registry(ROOT / "config" / "classes.yaml")
    ruleset = load_rule_set(ROOT / "config" / "rules.yaml", registry)
    findings = []
    for path in discover_sources(SYNTHETIC):
        profile = profile_source(path, SYNTHETIC)
        key = source_key_for(path, SYNTHETIC)
        findings.extend(detect_candidates(profile, ruleset, source_key=key))
    return registry, ruleset, findings


# ---------------------------------------------------------------- doc <-> constants


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ("suffixes", contract.SUPPORTED_SUFFIXES),
        ("report-formats", contract.REPORT_FORMATS),
        ("class-ids", contract.CLASS_IDS),
        ("scan-states", contract.SCAN_STATES),
        ("scan-stages", contract.SCAN_STAGES),
        ("source-error-codes", contract.SOURCE_ERROR_CODES),
        ("source-warning-codes", contract.SOURCE_WARNING_CODES),
        ("finding-sort-keys", contract.FINDING_SORT_KEYS),
        ("metadata-statuses", contract.METADATA_STATUSES),
        ("explanation-kinds", contract.EXPLANATION_KINDS),
        ("review-statuses", contract.REVIEW_STATUSES),
    ],
)
def test_doc_lists_match_constants(block, expected):
    assert _bullets(block) == list(expected)


def test_doc_endpoint_table_matches_constants():
    rows = re.findall(r"^\| `([A-Z]+)` \| `([^`]+)` \|", _block("endpoints"), re.MULTILINE)
    assert rows == list(contract.ENDPOINTS)
    assert len(set(rows)) == len(rows)


def test_doc_error_table_matches_constants():
    rows = re.findall(r"^\| `([a-z_]+)` \| (\d{3}) \|", _block("error-codes"), re.MULTILINE)
    assert {code: int(status) for code, status in rows} == contract.ERROR_HTTP_STATUS
    assert len(rows) == len(contract.ERROR_HTTP_STATUS)


def test_doc_ingestion_limits_match_backend_defaults():
    documented = _kv("ingestion-limits")
    actual = {f.name: float(getattr(DEFAULT_LIMITS, f.name)) for f in dataclasses.fields(DEFAULT_LIMITS)}
    assert documented == actual


def test_doc_workspace_limits_match_defaults():
    documented = _kv("workspace-limits")
    actual = {
        k: float(v) for k, v in dataclasses.asdict(contract.WorkspaceLimits()).items()
    }
    assert documented == actual


# ---------------------------------------------------------------- constants <-> real backend


def test_class_ids_are_the_official_five_in_order():
    registry = load_class_registry(ROOT / "config" / "classes.yaml")
    assert registry.taxonomy_status == "confirmed"
    assert list(contract.CLASS_IDS) == list(registry.ids())
    assert len(contract.CLASS_IDS) == 5


def test_metadata_statuses_match_backend_enum():
    assert list(contract.METADATA_STATUSES) == [s.value for s in DeclarationStatus]


def test_suffixes_and_origins_match_backend():
    assert list(contract.SUPPORTED_SUFFIXES) == sorted(BACKEND_SUFFIXES)
    assert tuple(contract.METADATA_ORIGINS) == tuple(BACKEND_ORIGINS)


def test_metadata_byte_limit_matches_strict_yaml_cap():
    assert contract.WorkspaceLimits().max_metadata_bytes == MAX_CONFIG_BYTES


@pytest.mark.parametrize("fmt", contract.REPORT_FORMATS)
def test_each_report_format_is_produced_by_real_write_reports(tmp_path, fmt):
    registry = load_class_registry(ROOT / "config" / "classes.yaml")
    ruleset = load_rule_set(ROOT / "config" / "rules.yaml", registry)
    written = write_reports((), {}, None, registry, ruleset, tmp_path, [fmt])
    assert len(written) == 1 and written[0].is_file()


def test_every_backend_error_class_has_a_source_error_code_decision():
    backend_classes = {
        obj
        for _, obj in inspect.getmembers(backend_errors, inspect.isclass)
        if issubclass(obj, backend_errors.FieldLedgerError) and obj.__module__ == backend_errors.__name__
    }
    # Classes that never reach the per-source path, or are catch-all bases, map to "ingest_failed".
    catch_all = {
        backend_errors.FieldLedgerError,
        backend_errors.IngestionError,
        backend_errors.ConfigError,
    }
    unassigned = backend_classes - set(contract.SOURCE_ERROR_CODE_BY_EXCEPTION) - catch_all
    assert not unassigned, f"assign a source error code to: {sorted(c.__name__ for c in unassigned)}"
    assert set(contract.SOURCE_ERROR_CODE_BY_EXCEPTION.values()) <= set(contract.SOURCE_ERROR_CODES)
    assert "ingest_failed" in contract.SOURCE_ERROR_CODES


def test_source_error_code_uses_class_not_message():
    err = backend_errors.MalformedCsvError("this message mentions invalid_database")
    assert contract.source_error_code(err) == "malformed_csv"
    assert contract.source_error_code(backend_errors.IngestionError("x")) == "ingest_failed"
    assert contract.source_error_code(ValueError("x")) == "ingest_failed"


# ---------------------------------------------------------------- finding IDs


def test_finding_id_shape_and_determinism():
    a = contract.finding_id("app.db", "kyc_records", "pan_card_number", "FINANCIAL_INFORMATION")
    assert re.fullmatch(r"[0-9a-f]{16}", a)
    assert a == contract.finding_id("app.db", "kyc_records", "pan_card_number", "FINANCIAL_INFORMATION")


def test_finding_id_regression_vector():
    # If this changes, review annotations saved under the old IDs would silently detach.
    assert (
        contract.finding_id("app.db", "kyc_records", "pan_card_number", "FINANCIAL_INFORMATION")
        == "31f2b291402f76ec"
    )


def test_finding_id_depends_on_every_field_and_is_unambiguous():
    base = ("a.csv", "t", "c", "PERSONAL_IDENTITY")
    ids = {contract.finding_id(*base)}
    for i, replacement in enumerate(("b.csv", "u", "d", "CONTACT_INFORMATION")):
        changed = list(base)
        changed[i] = replacement
        ids.add(contract.finding_id(*changed))
    assert len(ids) == 5
    # NUL separation: moving a character across a field boundary must change the ID.
    assert contract.finding_id("ab", "c", "d", "e") != contract.finding_id("a", "bc", "d", "e")


@pytest.mark.parametrize(
    "bad",
    [("", "t", "c", "X"), ("s", "t", "c", ""), ("s\x00", "t", "c", "X"), (None, "t", "c", "X")],
)
def test_finding_id_rejects_invalid_parts(bad):
    with pytest.raises(ValueError):
        contract.finding_id(*bad)


def test_finding_ids_are_unique_over_real_synthetic_findings():
    _, _, findings = _real_findings()
    assert findings, "synthetic scan produced no findings"
    ids = [contract.finding_id(*f.identity) for f in findings]
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------- JSON examples in the doc


def _doc_json_examples() -> list[dict]:
    return [json.loads(b) for b in re.findall(r"```json\n(.*?)\n```", _doc(), re.DOTALL)]


def test_doc_json_examples_are_valid_and_consistent_with_constants():
    examples = _doc_json_examples()
    assert len(examples) >= 2
    seen_finding = seen_scan = False
    for ex in examples:
        if "class_id" in ex:  # finding list item
            seen_finding = True
            assert ex["class_id"] in contract.CLASS_IDS
            assert set(ex["other_class_ids"]) <= set(contract.CLASS_IDS)
            assert ex["review_status"] in contract.REVIEW_STATUSES
            assert ex["metadata_status"] in contract.METADATA_STATUSES
            assert ex["id"] == contract.finding_id(
                ex["source_key"], ex["table"], ex["column"], ex["class_id"]
            )
        if "state" in ex:  # scan status
            seen_scan = True
            assert ex["state"] in contract.SCAN_STATES
            assert ex["stage"] in contract.SCAN_STAGES
            assert ex["sources_done"] <= ex["sources_total"]
            for err in ex["source_errors"]:
                assert err["code"] in contract.SOURCE_ERROR_CODES
    assert seen_finding and seen_scan


def test_doc_states_the_safety_defaults():
    doc = _doc()
    for phrase in ("127.0.0.1", "--allow-network", "never raised above 3600", "Nothing here is a legal determination"):
        assert phrase in doc.replace("\n", " "), phrase


# ---------------------------------------------------------------- WorkspaceLimits


def test_workspace_limits_defaults_are_valid():
    limits = contract.WorkspaceLimits()
    assert limits.idle_timeout_seconds == contract.IDLE_TIMEOUT_CEILING_SECONDS == 3600


@pytest.mark.parametrize("field", [f.name for f in dataclasses.fields(contract.WorkspaceLimits)])
@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "10", None])
def test_workspace_limits_reject_non_positive_integers(field, bad):
    with pytest.raises(backend_errors.ConfigError):
        contract.WorkspaceLimits(**{field: bad})


def test_idle_timeout_can_be_lowered_but_not_raised():
    assert contract.WorkspaceLimits(idle_timeout_seconds=1).idle_timeout_seconds == 1
    assert contract.WorkspaceLimits(idle_timeout_seconds=3600).idle_timeout_seconds == 3600
    with pytest.raises(backend_errors.ConfigError):
        contract.WorkspaceLimits(idle_timeout_seconds=3601)


# ---------------------------------------------------------------- optional dependencies


_WEB_LIBS = re.compile(r"^\s*(?:from|import)\s+(fastapi|uvicorn|starlette|multipart|pydantic)\b", re.M)


def test_core_modules_and_contract_do_not_import_web_libraries():
    src = ROOT / "src" / "fieldledger"
    offenders = []
    for py in src.rglob("*.py"):
        rel = py.relative_to(src)
        # Application modules added later may import FastAPI; these two never may.
        in_web = rel.parts[0] == "web" and rel.name not in {"__init__.py", "contract.py"}
        if not in_web and _WEB_LIBS.search(py.read_text(encoding="utf-8")):
            offenders.append(rel.as_posix())
    assert offenders == []


@pytest.mark.parametrize("module", ["fieldledger.web.contract", "fieldledger.cli", "fieldledger"])
def test_imports_succeed_with_web_libraries_blocked(module):
    code = (
        "import sys\n"
        "for name in ('fastapi','uvicorn','starlette','multipart','pydantic'):\n"
        "    sys.modules[name] = None\n"
        f"import {module}\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr[-500:]


def test_pyproject_keeps_core_dependencies_and_web_extra_separate():
    tomllib = pytest.importorskip("tomllib")
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    assert project["dependencies"] == ["PyYAML"]

    def names(reqs):
        return {re.split(r"[<>=!~\[ ]", r, maxsplit=1)[0].lower() for r in reqs}

    extras = project["optional-dependencies"]
    assert names(extras["web"]) == {"fastapi", "uvicorn", "python-multipart"}
    assert names(extras["dev"]) == {"pytest"}
    assert not names(extras["dev"]) & names(extras["web"])
    assert "fieldledger" in names(extras["web-dev"]) and "httpx" in names(extras["web-dev"])


# ---------------------------------------------------------------- .gitignore behaviour


def _git_available() -> bool:
    if shutil.which("git") is None:
        return False
    r = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True, text=True
    )
    return r.returncode == 0


def _ignored(path: str) -> bool:
    r = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT, capture_output=True
    )
    assert r.returncode in (0, 1), r.stderr
    return r.returncode == 0


@pytest.mark.skipif(not _git_available(), reason="not inside a git work tree")
@pytest.mark.parametrize(
    "path",
    [
        "frontend/node_modules/react/index.js",
        "frontend/dist/index.html",
        "frontend/playwright-report/index.html",
        "frontend/test-results/x.json",
        "src/fieldledger/web/static/index.html",
        "uploads/customers.csv",
        "workspace/session/app.db",
        ".fieldledger/workspace/reviews.json",
        "output/milestone6/fieldledger_report.json",
        "fieldledger_report.json",
        "fieldledger_inventory.csv",
        "fieldledger_review_annotations.json",
        ".env",
        ".env.local",
        "frontend/.env",
        "frontend/.env.local",
        ".venv/pyvenv.cfg",
    ],
)
def test_user_data_secrets_and_build_output_are_ignored(path):
    assert _ignored(path), path


@pytest.mark.skipif(not _git_available(), reason="not inside a git work tree")
@pytest.mark.parametrize(
    "path",
    [
        "frontend/src/main.tsx",
        "frontend/package.json",
        "frontend/package-lock.json",
        "frontend/.env.example",
        ".env.example",
        "docs/api-contract.md",
        "src/fieldledger/web/app.py",
        "tests/web/test_contract.py",
        "data/synthetic/app.db",
        "data/synthetic/ground_truth/labels.yaml",
        # The team's own untracked work must stay visible to git, never silently ignored.
        "files.zip",
        "files (1).zip",
        "fieldledger-m4.zip",
        "fieldledger-m4-official.zip",
        "fieldledger_milestone1.zip",
        "M4_CHANGES.md",
        "M4_OFFICIAL_CHANGES.md",
        "PS-019.pdf",
        "milestone2_inspection.txt",
    ],
)
def test_source_and_team_files_are_not_ignored(path):
    assert not _ignored(path), path
