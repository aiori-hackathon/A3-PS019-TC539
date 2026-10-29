"""Milestone 5: purpose/retention metadata loading, linking and review-gap reporting.

Unit tests use small hand-built profiles and findings. The integration tests at the bottom use
the committed synthetic dataset and the real configuration. Ground-truth labels appear ONLY in
the last section, as a test oracle: production code in metadata.py never touches them.
"""

import json
import logging
import random
from pathlib import Path

import pytest

from fieldledger import metadata as metadata_module
from fieldledger.classes import load_class_registry
from fieldledger.detector import detect_candidates
from fieldledger.discovery import discover_sources
from fieldledger.errors import ConfigError
from fieldledger.evaluation import load_labels
from fieldledger.findings import EVIDENCE_NOTICE, Finding, source_key_for
from fieldledger.metadata import (
    GAP_NOTICE,
    MAX_DECLARATION_CHARS,
    MAX_NAME_CHARS,
    DeclarationStatus,
    MetadataEntry,
    MetadataSet,
    UnmatchedReason,
    link_metadata,
    load_metadata,
)
from fieldledger.profiler import profile_source
from fieldledger.profiles import (
    BroadType,
    ColumnProfile,
    SourceProfile,
    SourceType,
    TableProfile,
    TypeBasis,
)
from fieldledger.rules import load_rule_set

REPO_ROOT = Path(__file__).resolve().parents[2]

REGISTRY_TEXT = """\
taxonomy_status: provisional
required_class_count: 2
classes:
  - {id: ALPHA_1, label: Alpha}
  - {id: BETA_2, label: Beta}
"""
RULES_TEXT = """\
rules_status: provisional
rules:
  - {id: R_A, class_id: ALPHA_1, match: token, patterns: [alp]}
  - {id: R_B, class_id: BETA_2, match: token, patterns: [bet]}
"""

SECRET_PURPOSE = "SECRET-PURPOSE-TEXT-9x"
SECRET_RETENTION = "SECRET-RETENTION-TEXT-9x"


# ------------------------------------------------------------------------------- helpers


def write(tmp_path, text, name="metadata.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def write_meta(tmp_path, entries, origin="user_provided", name="metadata.yaml"):
    """JSON is valid YAML, so json.dumps gives exact control over odd values."""
    return write(tmp_path, json.dumps({"metadata_origin": origin, "entries": entries}), name)


def entry(source="a.csv", table="t", column="c1", **declarations):
    return {"source_key": source, "table": table, "column": column, **declarations}


def make_profile(tables, name="a.csv"):
    return SourceProfile(
        source_type=SourceType.CSV,
        source_name=name,
        type_basis=TypeBasis.OBSERVED_VALUES,
        tables=tuple(
            TableProfile(
                name=table,
                columns=tuple(ColumnProfile(c, i + 1, BroadType.STRING) for i, c in enumerate(cols)),
            )
            for table, cols in tables.items()
        ),
    )


def make_finding(source, table, column, position, class_id="ALPHA_1"):
    return Finding(
        source_key=source, table_name=table, column_name=column,
        column_position=position, class_id=class_id, rule_ids=("R_A",),
    )


@pytest.fixture
def registry(tmp_path):
    return load_class_registry(write(tmp_path, REGISTRY_TEXT, "registry.yaml"))


@pytest.fixture
def ruleset(tmp_path, registry):
    return load_rule_set(write(tmp_path, RULES_TEXT, "rules.yaml"), registry)


def link(findings, entries_path, profiles, registry, ruleset):
    return link_metadata(
        findings, load_metadata(entries_path), profiles, registry=registry, ruleset=ruleset
    )


def by_column(report):
    return {r.column_name: r for r in report.linked_fields}


def all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_keys(v)


# --------------------------------------------------------------------- loading: valid input


def test_load_valid_file_parses_entries_sorted_and_normalised(tmp_path):
    path = write_meta(
        tmp_path,
        [
            entry(column="b", purpose="  Order\n fulfilment  ", retention="24 months"),
            entry(column="a", purpose="x"),
        ],
        origin="organizer_provided",
    )
    loaded = load_metadata(path)
    assert loaded.metadata_origin == "organizer_provided"
    assert [e.column_name for e in loaded.entries] == ["a", "b"]  # sorted by identity
    b = loaded.entries[1]
    assert b.purpose == "Order fulfilment"  # whitespace runs and line breaks collapse
    assert b.has_purpose and b.has_retention
    assert not loaded.entries[0].has_retention


def test_empty_entries_list_is_valid_and_means_no_metadata(tmp_path):
    loaded = load_metadata(write_meta(tmp_path, []))
    assert loaded.entries == ()


@pytest.mark.parametrize("blank", ["", "   ", "\t\n ", None])
def test_blank_whitespace_and_null_declarations_are_missing(tmp_path, blank):
    loaded = load_metadata(write_meta(tmp_path, [entry(purpose=blank, retention=blank)]))
    e = loaded.entries[0]
    assert e.purpose is None and e.retention is None
    assert not e.has_purpose and not e.has_retention


def test_yaml_empty_value_and_tilde_are_missing_and_absent_key_is_missing(tmp_path):
    text = (
        "metadata_origin: user_provided\n"
        "entries:\n"
        "  - {source_key: a.csv, table: t, column: c1, purpose: , retention: ~}\n"
        "  - {source_key: a.csv, table: t, column: c2}\n"
    )
    loaded = load_metadata(write(tmp_path, text))
    assert all(not e.has_purpose and not e.has_retention for e in loaded.entries)


def test_placeholder_text_counts_as_declared_not_judged(tmp_path):
    e = load_metadata(write_meta(tmp_path, [entry(purpose="TBD", retention="N/A")])).entries[0]
    assert e.has_purpose and e.has_retention


def test_entries_differing_only_by_case_are_distinct_not_duplicates(tmp_path):
    loaded = load_metadata(write_meta(tmp_path, [entry(column="Email"), entry(column="email")]))
    assert len(loaded.entries) == 2


# ------------------------------------------------------------------- loading: invalid input


@pytest.mark.parametrize(
    "bad_key",
    ["/abs/x.csv", "../x.csv", "a/../b.csv", "a\\b.csv", "C:/x.csv", "", "a//b.csv",
     "./a.csv", "a/./b.csv", "ctl\u0007x.csv", 5, None],
)
def test_invalid_source_keys_are_rejected(tmp_path, bad_key):
    with pytest.raises(ConfigError, match="source_key is invalid"):
        load_metadata(write_meta(tmp_path, [entry(source=bad_key)]))


@pytest.mark.parametrize("bad", ["", None, 5, "x" * (MAX_NAME_CHARS + 1)])
@pytest.mark.parametrize("which", ["table", "column"])
def test_invalid_table_and_column_names_are_rejected(tmp_path, which, bad):
    item = entry()
    item[which] = bad
    with pytest.raises(ConfigError, match=which):
        load_metadata(write_meta(tmp_path, [item]))


@pytest.mark.parametrize(
    "bad", [5, 1.5, True, ["a"], {"a": 1}, "ctl\u0007text", "x" * (MAX_DECLARATION_CHARS + 1)]
)
@pytest.mark.parametrize("which", ["purpose", "retention"])
def test_invalid_declaration_values_are_rejected(tmp_path, which, bad):
    with pytest.raises(ConfigError, match=which):
        load_metadata(write_meta(tmp_path, [entry(**{which: bad})]))


def test_duplicate_entries_for_one_column_are_rejected(tmp_path):
    path = write_meta(tmp_path, [entry(purpose="a"), entry(purpose="b")])
    with pytest.raises(ConfigError, match="duplicate metadata entry at entries\\[1\\]"):
        load_metadata(path)


def test_same_column_in_different_tables_or_sources_is_not_a_duplicate(tmp_path):
    loaded = load_metadata(
        write_meta(tmp_path, [entry(table="t1"), entry(table="t2"), entry(source="b.csv")])
    )
    assert len(loaded.entries) == 3


def test_unknown_entry_keys_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unknown keys"):
        load_metadata(write_meta(tmp_path, [entry(owner="someone")]))


def test_unknown_top_level_keys_are_rejected(tmp_path):
    text = "metadata_origin: user_provided\nentries: []\nextra: 1\n"
    with pytest.raises(ConfigError, match="unknown top-level keys"):
        load_metadata(write(tmp_path, text))


@pytest.mark.parametrize("missing", ["source_key", "table", "column"])
def test_missing_required_entry_keys_are_rejected(tmp_path, missing):
    item = entry()
    del item[missing]
    with pytest.raises(ConfigError, match="missing required keys"):
        load_metadata(write_meta(tmp_path, [item]))


@pytest.mark.parametrize("origin", ["unknown", None, "", 5])
def test_unsupported_metadata_origin_is_rejected(tmp_path, origin):
    with pytest.raises(ConfigError, match="metadata_origin"):
        load_metadata(write_meta(tmp_path, [], origin=origin))


@pytest.mark.parametrize("entries", [None, "x", {"a": 1}, 5])
def test_entries_must_be_a_list(tmp_path, entries):
    text = json.dumps({"metadata_origin": "user_provided", "entries": entries})
    with pytest.raises(ConfigError, match="entries must be a list"):
        load_metadata(write(tmp_path, text))


def test_missing_entries_key_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="entries must be a list"):
        load_metadata(write(tmp_path, "metadata_origin: user_provided\n"))


def test_entry_must_be_a_mapping(tmp_path):
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_metadata(write_meta(tmp_path, ["not a mapping"]))


def test_too_many_entries_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(metadata_module, "MAX_METADATA_ENTRIES", 1)
    with pytest.raises(ConfigError, match="too many"):
        load_metadata(write_meta(tmp_path, [entry(column="a"), entry(column="b")]))


@pytest.mark.parametrize(
    "text",
    [
        'metadata_origin: user_provided\nentries: [ {source_key: "a.csv"\n',  # unclosed
        "metadata_origin: user_provided\nentries: &a []\nother: *a\n",  # alias
        "metadata_origin: user_provided\nmetadata_origin: user_provided\nentries: []\n",  # dup key
        "metadata_origin: user_provided\nentries:\n  - {source_key: a.csv, table: t, column: c,"
        " purpose: x, purpose: y}\n",  # duplicate key inside an entry
        "- a\n- b\n",  # top level is a list
        "",  # empty file
        "entries: [!!python/object/apply:os.getcwd []]\nmetadata_origin: user_provided\n",  # unsafe tag
    ],
)
def test_malformed_or_unsafe_yaml_is_a_config_error(tmp_path, text):
    with pytest.raises(ConfigError):
        load_metadata(write(tmp_path, text))


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_metadata(tmp_path / "nope.yaml")


def test_load_error_messages_never_echo_names_keys_or_text(tmp_path):
    secret = "SECRETNAME_7f3"
    cases = [
        [entry(column=secret, purpose=5)],  # bad declaration type
        [entry(column=secret, **{secret + "_key": "x"})],  # unknown key
        [entry(column=secret, purpose="a"), entry(column=secret, purpose="b")],  # duplicate
        [entry(source=secret + "/../x", column=secret)],  # invalid source key
        [entry(column=secret, purpose="ctl\u0007" + secret)],  # control character
    ]
    for entries in cases:
        with pytest.raises(ConfigError) as info:
            load_metadata(write_meta(tmp_path, entries))
        assert secret not in str(info.value)
        assert str(tmp_path) not in str(info.value)


def test_direct_construction_is_validated_too():
    with pytest.raises(ValueError):
        MetadataEntry("../x", "t", "c")
    with pytest.raises(ValueError):
        MetadataEntry("a.csv", "t", "c", purpose=5)
    with pytest.raises(ValueError, match="duplicate"):
        MetadataSet("user_provided", (MetadataEntry("a.csv", "t", "c"), MetadataEntry("a.csv", "t", "c")))
    with pytest.raises(ValueError):
        MetadataSet("bogus", ())
    assert MetadataEntry("a.csv", "t", "c", purpose="   ").purpose is None


# ------------------------------------------------------------------------ linking: statuses


@pytest.fixture
def scenario(tmp_path, registry, ruleset):
    profiles = {"a.csv": make_profile({"t": ["c1", "c2", "c3", "c4", "c5", "unflagged"]})}
    findings = [make_finding("a.csv", "t", f"c{i}", i) for i in range(1, 6)]
    path = write_meta(
        tmp_path,
        [
            entry(column="c1", purpose=SECRET_PURPOSE, retention=SECRET_RETENTION),
            entry(column="c2", purpose=SECRET_PURPOSE),
            entry(column="c3", retention=SECRET_RETENTION),
            entry(column="c4", purpose="  ", retention=None),  # present but declares nothing
            # c5: no entry at all
            entry(column="unflagged", purpose=SECRET_PURPOSE, retention=SECRET_RETENTION),
            entry(source="gone.csv", table="t", column="c1", purpose=SECRET_PURPOSE),
        ],
    )
    return findings, path, profiles, registry, ruleset


def test_every_declaration_status_is_reported(scenario):
    findings, path, profiles, registry, ruleset = scenario
    report = link(findings, path, profiles, registry, ruleset)
    rows = by_column(report)
    assert rows["c1"].status is DeclarationStatus.COMPLETE
    assert rows["c2"].status is DeclarationStatus.MISSING_RETENTION
    assert rows["c3"].status is DeclarationStatus.MISSING_PURPOSE
    assert rows["c4"].status is DeclarationStatus.MISSING_BOTH
    assert rows["c5"].status is DeclarationStatus.NO_ENTRY
    assert (rows["c2"].has_purpose, rows["c2"].has_retention) == (True, False)
    assert (rows["c3"].has_purpose, rows["c3"].has_retention) == (False, True)
    assert (rows["c5"].has_purpose, rows["c5"].has_retention) == (False, False)
    assert [r.column_name for r in report.gap_fields] == ["c2", "c3", "c4", "c5"]


def test_summary_counts_are_consistent(scenario):
    findings, path, profiles, registry, ruleset = scenario
    s = link(findings, path, profiles, registry, ruleset).summary
    assert s.flagged_columns == 5 and s.ambiguous_columns == 0
    assert dict(s.by_status) == {
        "complete": 1, "missing_purpose": 1, "missing_retention": 1, "missing_both": 1, "no_entry": 1,
    }
    assert [name for name, _ in s.by_status] == [x.value for x in DeclarationStatus]
    assert sum(n for _, n in s.by_status) == s.flagged_columns
    assert (s.metadata_entries, s.orphan_entries, s.unmatched_entries) == (6, 1, 1)


def test_report_carries_provenance_statuses(scenario):
    findings, path, profiles, registry, ruleset = scenario
    report = link(findings, path, profiles, registry, ruleset)
    assert (report.metadata_origin, report.taxonomy_status, report.rules_status) == (
        "user_provided", "provisional", "provisional",
    )


def test_blank_declarations_are_missing_end_to_end(tmp_path, registry, ruleset):
    profiles = {"a.csv": make_profile({"t": ["c1", "c2"]})}
    findings = [make_finding("a.csv", "t", "c1", 1), make_finding("a.csv", "t", "c2", 2)]
    path = write_meta(
        tmp_path,
        [entry(column="c1", purpose="   ", retention="24 months"), entry(column="c2", purpose="x", retention="")],
    )
    rows = by_column(link(findings, path, profiles, registry, ruleset))
    assert rows["c1"].status is DeclarationStatus.MISSING_PURPOSE
    assert rows["c2"].status is DeclarationStatus.MISSING_RETENTION


def test_no_metadata_at_all_makes_every_flagged_column_no_entry(tmp_path, registry, ruleset):
    profiles = {"a.csv": make_profile({"t": ["c1", "c2"]})}
    findings = [make_finding("a.csv", "t", "c1", 1), make_finding("a.csv", "t", "c2", 2)]
    report = link(findings, write_meta(tmp_path, []), profiles, registry, ruleset)
    assert {r.status for r in report.linked_fields} == {DeclarationStatus.NO_ENTRY}
    assert report.orphan_entries == () and report.unmatched_entries == ()


def test_no_findings_gives_an_empty_inventory_and_still_reports_entries(tmp_path, registry, ruleset):
    profiles = {"a.csv": make_profile({"t": ["c1"]})}
    path = write_meta(tmp_path, [entry(column="c1", purpose="x"), entry(column="zzz", purpose="x")])
    report = link([], path, profiles, registry, ruleset)
    assert report.linked_fields == ()
    assert [e.column_name for e in report.orphan_entries] == ["c1"]
    assert [u.entry.column_name for u in report.unmatched_entries] == ["zzz"]


# --------------------------------------------------------------- linking: ambiguity, identity


def test_ambiguous_finding_is_one_row_with_every_class(tmp_path, registry, ruleset):
    profiles = {"a.csv": make_profile({"t": ["c1", "c2"]})}
    findings = [
        make_finding("a.csv", "t", "c1", 1, "BETA_2"),
        make_finding("a.csv", "t", "c1", 1, "ALPHA_1"),
        make_finding("a.csv", "t", "c2", 2, "ALPHA_1"),
    ]
    path = write_meta(tmp_path, [entry(column="c1", purpose="p", retention="r")])
    report = link(findings, path, profiles, registry, ruleset)
    assert len(report.linked_fields) == 2  # one per column, not per finding
    c1 = by_column(report)["c1"]
    assert c1.class_ids == ("ALPHA_1", "BETA_2") and c1.ambiguous
    assert c1.status is DeclarationStatus.COMPLETE  # metadata is class-agnostic
    assert not by_column(report)["c2"].ambiguous
    assert report.summary.flagged_columns == 2 and report.summary.ambiguous_columns == 1


def test_same_file_name_in_different_directories_is_never_merged(tmp_path, registry, ruleset):
    profiles = {
        "crm/customers.csv": make_profile({"customers": ["email"]}, "customers.csv"),
        "crm/archive/customers.csv": make_profile({"customers": ["email"]}, "customers.csv"),
    }
    findings = [
        make_finding("crm/customers.csv", "customers", "email", 1),
        make_finding("crm/archive/customers.csv", "customers", "email", 1),
    ]
    path = write_meta(
        tmp_path,
        [entry(source="crm/customers.csv", table="customers", column="email", purpose="p", retention="r")],
    )
    rows = {r.source_key: r for r in link(findings, path, profiles, registry, ruleset).linked_fields}
    assert rows["crm/customers.csv"].status is DeclarationStatus.COMPLETE
    assert rows["crm/archive/customers.csv"].status is DeclarationStatus.NO_ENTRY


def test_same_column_name_in_different_tables_is_never_merged(tmp_path, registry, ruleset):
    profiles = {"a.csv": make_profile({"t1": ["email"], "t2": ["email"]})}
    findings = [make_finding("a.csv", "t1", "email", 1), make_finding("a.csv", "t2", "email", 1)]
    path = write_meta(tmp_path, [entry(table="t1", column="email", purpose="p", retention="r")])
    rows = {r.table_name: r for r in link(findings, path, profiles, registry, ruleset).linked_fields}
    assert rows["t1"].status is DeclarationStatus.COMPLETE
    assert rows["t2"].status is DeclarationStatus.NO_ENTRY


# ------------------------------------------------------------- linking: orphan and unmatched


def test_orphan_entry_is_declared_but_not_flagged(scenario):
    findings, path, profiles, registry, ruleset = scenario
    report = link(findings, path, profiles, registry, ruleset)
    assert [e.column_name for e in report.orphan_entries] == ["unflagged"]
    assert "unflagged" not in by_column(report)  # it is not an inventory row


def test_unmatched_entries_are_reported_with_a_reason_and_do_not_abort(tmp_path, registry, ruleset):
    profiles = {"a.csv": make_profile({"t": ["c1"]})}
    findings = [make_finding("a.csv", "t", "c1", 1)]
    path = write_meta(
        tmp_path,
        [
            entry(column="c1", purpose="p", retention="r"),
            entry(source="gone.csv", column="c1"),  # source not in the scan
            entry(table="gone_table", column="c1"),  # table not in the scan
            entry(column="gone_column"),  # column not in the scan
        ],
    )
    report = link(findings, path, profiles, registry, ruleset)
    assert by_column(report)["c1"].status is DeclarationStatus.COMPLETE  # scan still completes
    reasons = {(u.entry.source_key, u.entry.table_name, u.entry.column_name): u.reason for u in report.unmatched_entries}
    assert reasons == {
        ("gone.csv", "t", "c1"): UnmatchedReason.SOURCE_NOT_SCANNED,
        ("a.csv", "gone_table", "c1"): UnmatchedReason.TABLE_NOT_FOUND,
        ("a.csv", "t", "gone_column"): UnmatchedReason.COLUMN_NOT_FOUND,
    }
    assert not any(u.case_variant_exists for u in report.unmatched_entries)
    assert report.orphan_entries == ()


def test_matching_is_exact_and_case_sensitive_with_a_case_hint(tmp_path, registry, ruleset):
    """SQLite treats names case-insensitively; FieldLedger matches the profiled strings exactly."""
    profiles = {"a.csv": make_profile({"t": ["email"]}), "B.csv": make_profile({"t": ["x"]}, "B.csv")}
    findings = [make_finding("a.csv", "t", "email", 1)]
    path = write_meta(
        tmp_path,
        [
            entry(column="Email", purpose="p", retention="r"),  # column differs by case only
            entry(source="b.csv", column="x", purpose="p", retention="r"),  # source differs by case only
            entry(column="other", purpose="p", retention="r"),  # genuinely absent
        ],
    )
    report = link(findings, path, profiles, registry, ruleset)
    assert by_column(report)["email"].status is DeclarationStatus.NO_ENTRY  # never auto-merged
    hints = {u.entry.column_name: u.case_variant_exists for u in report.unmatched_entries}
    assert hints == {"Email": True, "x": True, "other": False}
    reasons = {u.entry.column_name: u.reason for u in report.unmatched_entries}
    assert reasons["x"] is UnmatchedReason.SOURCE_NOT_SCANNED


# ------------------------------------------------------------------- linking: input checks


def test_bad_findings_and_metadata_inputs_are_rejected(tmp_path, registry, ruleset):
    profiles = {"a.csv": make_profile({"t": ["c1"]})}
    meta = load_metadata(write_meta(tmp_path, []))
    good = make_finding("a.csv", "t", "c1", 1)

    def run(findings, metadata=meta):
        return link_metadata(findings, metadata, profiles, registry=registry, ruleset=ruleset)

    with pytest.raises(ValueError, match="duplicate finding"):
        run([good, good])
    with pytest.raises(ValueError, match="not in its profiled source"):
        run([make_finding("a.csv", "t", "nope", 2)])
    with pytest.raises(ValueError, match="not in its profiled source"):
        run([make_finding("zzz.csv", "t", "c1", 1)])
    with pytest.raises(ValueError, match="not in the registry"):
        run([make_finding("a.csv", "t", "c1", 1, "NOT_A_CLASS")])
    with pytest.raises(TypeError):
        run(["not a finding"])
    with pytest.raises(TypeError):
        run([good], metadata={"entries": []})


# --------------------------------------------------------------------------- determinism


def test_output_is_deterministic_and_independent_of_input_order(tmp_path, registry, ruleset):
    profiles = {
        "a.csv": make_profile({"t": ["c1", "c2", "c3"], "u": ["c1"]}),
        "b/a.csv": make_profile({"t": ["c1", "c2"]}),
    }
    findings = [
        make_finding(s, t, c, p)
        for s, t, c, p in [
            ("a.csv", "t", "c1", 1), ("a.csv", "t", "c2", 2), ("a.csv", "u", "c1", 1),
            ("b/a.csv", "t", "c1", 1), ("b/a.csv", "t", "c2", 2),
        ]
    ]
    entries = [
        entry(column="c1", purpose="p", retention="r"), entry(column="c3", purpose="p"),
        entry(source="b/a.csv", column="c2", retention="r"), entry(source="gone.csv"),
        entry(table="gone", column="zz"),
    ]

    def render(seed):
        rng = random.Random(seed)
        shuffled_findings, shuffled_entries = findings[:], entries[:]
        rng.shuffle(shuffled_findings)
        rng.shuffle(shuffled_entries)
        report = link(shuffled_findings, write_meta(tmp_path, shuffled_entries), profiles, registry, ruleset)
        return json.dumps(report.to_dict(include_declarations=True))

    renders = {render(seed) for seed in range(6)}
    assert len(renders) == 1
    rows = json.loads(renders.pop())["linked_fields"]
    keys = [(r["source_key"], r["table"], r["position"], r["column"]) for r in rows]
    assert keys == sorted(keys)  # stable documented order


# ----------------------------------------------------------------- privacy and notices


def test_default_report_has_no_declaration_text_and_opt_in_adds_it(scenario):
    findings, path, profiles, registry, ruleset = scenario
    report = link(findings, path, profiles, registry, ruleset)
    default = json.dumps(report.to_dict())
    assert SECRET_PURPOSE not in default and SECRET_RETENTION not in default
    assert '"purpose"' not in default and '"retention"' not in default  # not even a null key
    assert report.to_dict()["declarations_included"] is False
    assert report.to_dict() == report.to_dict(include_declarations=False)  # opt-in is explicit

    opted = report.to_dict(include_declarations=True)
    text = json.dumps(opted)
    assert opted["declarations_included"] is True
    assert SECRET_PURPOSE in text and SECRET_RETENTION in text
    rows = {r["column"]: r for r in opted["linked_fields"]}
    assert rows["c1"]["purpose"] == SECRET_PURPOSE and rows["c5"]["purpose"] is None
    assert opted["orphan_entries"][0]["purpose"] == SECRET_PURPOSE
    assert opted["unmatched_entries"][0]["purpose"] == SECRET_PURPOSE


@pytest.mark.parametrize("bad", ["yes", 1, None, "False"])
def test_include_declarations_must_be_an_explicit_bool(scenario, bad):
    findings, path, profiles, registry, ruleset = scenario
    report = link(findings, path, profiles, registry, ruleset)
    for obj in (report, report.linked_fields[0], report.orphan_entries[0], report.unmatched_entries[0]):
        with pytest.raises(TypeError):
            obj.to_dict(include_declarations=bad)


def test_declaration_text_is_hidden_from_repr(scenario):
    findings, path, profiles, registry, ruleset = scenario
    metadata = load_metadata(path)
    report = link_metadata(findings, metadata, profiles, registry=registry, ruleset=ruleset)
    for obj in (metadata, metadata.entries[0], report, report.linked_fields[0],
                report.orphan_entries[0], report.unmatched_entries[0]):
        text = repr(obj)
        assert SECRET_PURPOSE not in text and SECRET_RETENTION not in text


def test_report_contains_no_absolute_paths_and_logs_hold_no_names_or_text(scenario, tmp_path, caplog):
    findings, path, profiles, registry, ruleset = scenario
    with caplog.at_level(logging.DEBUG, logger="fieldledger"):
        report = link(findings, path, profiles, registry, ruleset)
    dumped = json.dumps(report.to_dict(include_declarations=True))
    assert str(tmp_path) not in dumped and str(path) not in dumped
    assert "metadata link" in caplog.text  # something is logged, only counts
    for forbidden in (SECRET_PURPOSE, SECRET_RETENTION, "a.csv", "unflagged", "c1", "metadata.yaml", str(tmp_path)):
        assert forbidden not in caplog.text


def test_report_carries_both_notices_and_no_legal_fields(scenario):
    findings, path, profiles, registry, ruleset = scenario
    data = link(findings, path, profiles, registry, ruleset).to_dict()
    assert data["technical_evidence_notice"] == EVIDENCE_NOTICE
    assert data["review_gap_notice"] == GAP_NOTICE
    lowered = GAP_NOTICE.lower()
    assert "review gap" in lowered
    assert "not evidence of a legal violation or non-compliance" in lowered
    assert "complete declaration is not evidence of compliance" in lowered
    assert "human review is required" in lowered
    forbidden = ("lawful", "legal", "complian", "consent", "violation", "verdict", "risk")
    for key in all_keys(data):
        assert not any(word in key.lower() for word in forbidden), key
    statuses = {r["status"] for r in data["linked_fields"]}
    assert not any(any(word in s for word in forbidden) for s in statuses)


def test_metadata_module_never_touches_ground_truth_code():
    source = (REPO_ROOT / "src" / "fieldledger" / "metadata.py").read_text(encoding="utf-8")
    for needle in ("from .evaluation", "import evaluation", "load_labels", "ground_truth", "labels.yaml"):
        assert needle not in source


# ------------------------------------------------- integration on the committed dataset


DATASET = REPO_ROOT / "data" / "synthetic"


@pytest.fixture(scope="module")
def real_registry():
    return load_class_registry(REPO_ROOT / "config" / "classes.yaml")


@pytest.fixture(scope="module")
def real_ruleset(real_registry):
    return load_rule_set(REPO_ROOT / "config" / "rules.yaml", real_registry)


@pytest.fixture(scope="module")
def real_scan(real_registry, real_ruleset):
    profiles, findings = {}, []
    for path in discover_sources(DATASET):
        key = source_key_for(path, DATASET)
        profiles[key] = profile_source(path, DATASET)
        findings.extend(detect_candidates(profiles[key], real_ruleset, source_key=key))
    return profiles, findings


@pytest.fixture(scope="module")
def real_metadata():
    return load_metadata(DATASET / "metadata" / "purpose_retention.yaml")


@pytest.fixture(scope="module")
def real_report(real_scan, real_metadata, real_registry, real_ruleset):
    profiles, findings = real_scan
    return link_metadata(findings, real_metadata, profiles, registry=real_registry, ruleset=real_ruleset)


def test_committed_metadata_file_loads(real_metadata):
    assert real_metadata.metadata_origin == "team_authored_synthetic"
    assert len(real_metadata.entries) == 129
    assert sum(e.has_purpose and e.has_retention for e in real_metadata.entries) == 102
    assert sum(e.has_retention and not e.has_purpose for e in real_metadata.entries) == 10
    assert sum(e.has_purpose and not e.has_retention for e in real_metadata.entries) == 17


def test_detector_output_links_with_recorded_gap_counts(real_report):
    s = real_report.summary
    assert s.flagged_columns == 146 and s.ambiguous_columns == 3
    assert dict(s.by_status) == {
        "complete": 84, "missing_purpose": 8, "missing_retention": 13, "missing_both": 0, "no_entry": 41,
    }
    assert (s.metadata_entries, s.orphan_entries, s.unmatched_entries) == (129, 24, 0)
    assert real_report.rules_status == "provisional" and real_report.taxonomy_status == "confirmed"
    assert len(real_report.gap_fields) == 146 - 84


def test_real_report_exports_no_cell_values_paths_or_declaration_text(real_report, real_metadata):
    exported = json.dumps(real_report.to_dict())
    values = set()
    for p in DATASET.rglob("*.csv"):
        for line in p.read_text(encoding="utf-8").splitlines()[1:]:
            values.update(v for v in line.split(",") if len(v) >= 10)
    assert len(values) > 100
    assert not any(v in exported for v in values)
    assert str(REPO_ROOT) not in exported and str(DATASET) not in exported
    declared = {t for e in real_metadata.entries for t in (e.purpose, e.retention) if t}
    assert len(declared) > 5
    assert not any(t in exported for t in declared)
    opted = json.dumps(real_report.to_dict(include_declarations=True))
    assert any(t in opted for t in declared)


def test_real_report_is_deterministic(real_scan, real_metadata, real_registry, real_ruleset):
    profiles, findings = real_scan
    runs = [
        json.dumps(
            link_metadata(findings, real_metadata, profiles, registry=real_registry, ruleset=real_ruleset).to_dict()
        )
        for _ in range(2)
    ]
    assert runs[0] == runs[1]


def test_ground_truth_oracle_confirms_the_join_but_is_never_used_to_build_it(
    real_scan, real_metadata, real_registry, real_ruleset, real_report
):
    """TEST-ONLY: labelled positives become pseudo-findings so the join can be checked
    independently of the detector. A real user never has these labels."""
    profiles, findings = real_scan
    labels = load_labels(DATASET / "ground_truth" / "labels.yaml", real_registry)
    position = {
        (key, t.name, c.name): c.position for key, p in profiles.items() for t in p.tables for c in t.columns
    }
    oracle = [
        Finding(source_key=s.source_key, table_name=t, column_name=c,
                column_position=position[(s.source_key, t, c)], class_id=cls, rule_ids=("TEST_ORACLE",))
        for s in labels.sources for (t, c, cls) in s.positives
    ]
    report = link_metadata(oracle, real_metadata, profiles, registry=real_registry, ruleset=real_ruleset)
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    expected = manifest["metadata_declarations_for_positive_columns"]
    got = dict(report.summary.by_status)
    assert got["complete"] == expected["complete"] == 102
    assert got["missing_purpose"] == expected["missing_purpose"] == 10
    assert got["missing_retention"] == expected["missing_retention"] == 17
    assert got["no_entry"] == expected["no_entry"] == 10 and got["missing_both"] == 0
    assert report.summary.flagged_columns == 139

    # The 24 orphans are labelled positives the name-only detector missed.
    truth = {(s.source_key, t, c) for s in labels.sources for (t, c, _) in s.positives}
    flagged = {f.column_identity for f in findings}
    orphans = {e.column_identity for e in real_report.orphan_entries}
    assert orphans == {c for c in truth - flagged if c in {e.column_identity for e in real_metadata.entries}}
    assert len(orphans) == 24
